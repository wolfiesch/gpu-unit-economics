"""Curated workload profiles and workload-aware GPU performance estimates.

The published benchmark table measures high-concurrency throughput.  This
module keeps that source data intact and applies explicit, conservative
workload factors for interactive, batch, and long-context serving.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from gpu_econ.benchmarks import throughput

GPU_MEMORY_GB = {"H100": 80.0, "H200": 141.0, "B200": 192.0}


@dataclass(frozen=True)
class ModelInputs:
    """Memory inputs for a model served with a fixed tensor-parallel layout."""

    model: str
    weights_gb: float
    runtime_overhead_gb_per_gpu: float
    kv_cache_gb_per_1k_tokens: float
    tensor_parallel_gpus: int

    def __post_init__(self) -> None:
        if min(self.weights_gb, self.runtime_overhead_gb_per_gpu) <= 0:
            raise ValueError("model memory inputs must be positive")
        if self.kv_cache_gb_per_1k_tokens < 0:
            raise ValueError("kv_cache_gb_per_1k_tokens must be non-negative")
        if self.tensor_parallel_gpus <= 0:
            raise ValueError("tensor_parallel_gpus must be positive")


@dataclass(frozen=True)
class WorkloadProfile:
    """A repeatable serving pattern used to adjust an offline benchmark."""

    id: str
    label: str
    model: str
    concurrent_requests: int
    input_tokens: int
    output_tokens: int
    max_latency_seconds: float | None
    throughput_factor: float
    decode_speed_factor: float
    utilization: float
    capacity_headroom: float

    def __post_init__(self) -> None:
        if self.concurrent_requests <= 0 or self.input_tokens <= 0 or self.output_tokens <= 0:
            raise ValueError("request and token counts must be positive")
        if self.max_latency_seconds is not None and self.max_latency_seconds <= 0:
            raise ValueError("max_latency_seconds must be positive when set")
        if not 0 < self.throughput_factor <= 1:
            raise ValueError("throughput_factor must be in (0, 1]")
        if not 0 < self.decode_speed_factor <= 1:
            raise ValueError("decode_speed_factor must be in (0, 1]")
        if not 0 < self.utilization <= 1:
            raise ValueError("utilization must be in (0, 1]")
        if not 0 <= self.capacity_headroom < 1:
            raise ValueError("capacity_headroom must be in [0, 1)")


@dataclass(frozen=True)
class FleetSizingInputs:
    """Values that can be passed into the existing fleet-sizing model."""

    tokens_per_sec: float
    utilization: float
    capacity_headroom: float


@dataclass(frozen=True)
class WorkloadEvaluation:
    """Compatibility and modeled performance for one GPU and workload."""

    gpu: str
    workload: str
    model: str
    compatible: bool
    effective_tokens_per_sec: float
    estimated_latency_seconds: float | None
    required_memory_gb: float
    available_memory_gb: float
    confidence: str
    provenance: str
    benchmark_kind: str
    reasons: tuple[str, ...]
    fleet_sizing_inputs: FleetSizingInputs

    @property
    def reason(self) -> str | None:
        """Single human-readable reason for simple clients."""
        return "; ".join(self.reasons) or None


MODELS = {
    "llama-2-70b": ModelInputs("llama-2-70b", 70.0, 6.0, 0.3125, 8),
    "llama-3.1-8b": ModelInputs("llama-3.1-8b", 8.0, 4.0, 0.0625, 1),
}

PROFILES = {
    "interactive": WorkloadProfile(
        "interactive", "Interactive chat", "llama-3.1-8b", 8, 2_048, 256, 5.0,
        0.32, 0.75, 0.55, 0.30,
    ),
    "batch": WorkloadProfile(
        "batch", "Batch generation", "llama-2-70b", 128, 1_024, 512, None,
        0.90, 1.0, 0.85, 0.15,
    ),
    "long-context": WorkloadProfile(
        "long-context", "Long-context analysis", "llama-3.1-8b", 4, 65_536, 1_024, 60.0,
        0.42, 0.45, 0.60, 0.25,
    ),
}


def _profile(workload: str | WorkloadProfile) -> WorkloadProfile:
    if isinstance(workload, WorkloadProfile):
        return workload
    try:
        return PROFILES[workload]
    except KeyError as exc:
        raise ValueError(f"unknown workload: {workload}") from exc


def _memory_required(model: ModelInputs, profile: WorkloadProfile) -> float:
    context_tokens = profile.input_tokens + profile.output_tokens
    kv_cache_total = (
        model.kv_cache_gb_per_1k_tokens
        * (context_tokens / 1_000)
        * profile.concurrent_requests
    )
    return (
        model.weights_gb / model.tensor_parallel_gpus
        + model.runtime_overhead_gb_per_gpu
        + kv_cache_total / model.tensor_parallel_gpus
    )


def _evaluate_gpu(profile: WorkloadProfile, gpu: str) -> WorkloadEvaluation:
    model = MODELS[profile.model]
    entry = throughput(gpu, profile.model)
    available_memory = GPU_MEMORY_GB[gpu]
    required_memory = _memory_required(model, profile)
    reasons: list[str] = []

    if entry is None:
        reasons.append(f"No {profile.model} benchmark is available for {gpu}.")
        effective_throughput = 0.0
        latency = None
        benchmark_kind = "unavailable"
        provenance = "unavailable"
        confidence = "none"
    else:
        effective_throughput = entry.tokens_per_sec * profile.throughput_factor
        # Published rows are offline/server throughput. A request-level decode
        # rate is conservatively modeled as 1/75 of that aggregate rate.
        request_decode_tps = entry.tokens_per_sec / 75 * profile.decode_speed_factor
        latency = profile.output_tokens / request_decode_tps
        benchmark_kind = entry.kind
        provenance = "measured" if entry.kind == "mlperf" else "estimated"
        confidence = "medium" if entry.kind == "mlperf" else "low"

    if required_memory > available_memory:
        reasons.append(
            f"Needs about {required_memory:.1f} GB per GPU, but {gpu} has "
            f"{available_memory:.0f} GB."
        )
    if (
        latency is not None
        and profile.max_latency_seconds is not None
        and latency > profile.max_latency_seconds
    ):
        reasons.append(
            f"Estimated {latency:.1f}s response latency exceeds the "
            f"{profile.max_latency_seconds:.0f}s workload target."
        )

    compatible = not reasons
    return WorkloadEvaluation(
        gpu=gpu,
        workload=profile.id,
        model=profile.model,
        compatible=compatible,
        effective_tokens_per_sec=effective_throughput,
        estimated_latency_seconds=latency,
        required_memory_gb=required_memory,
        available_memory_gb=available_memory,
        confidence=confidence,
        provenance=provenance,
        benchmark_kind=benchmark_kind,
        reasons=tuple(reasons),
        fleet_sizing_inputs=FleetSizingInputs(
            tokens_per_sec=effective_throughput,
            utilization=profile.utilization,
            capacity_headroom=profile.capacity_headroom,
        ),
    )


def evaluate(workload: str | WorkloadProfile) -> list[WorkloadEvaluation]:
    """Evaluate one curated/custom workload on every supported GPU."""
    profile = _profile(workload)
    return [_evaluate_gpu(profile, gpu) for gpu in GPU_MEMORY_GB]


def catalog() -> list[dict[str, object]]:
    """Return the curated profiles as JSON-shaped dictionaries."""
    return [asdict(profile) for profile in PROFILES.values()]
