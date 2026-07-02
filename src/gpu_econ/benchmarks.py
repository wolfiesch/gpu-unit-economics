"""Published per-GPU inference throughput estimates (tokens/sec) by model.

Tier-1 benchmark data. Each entry carries explicit provenance:

- kind="mlperf": derived from verified MLCommons MLPerf Inference datacenter
  results (closed division). The `derivation` states exactly how the per-GPU
  figure was computed from the submitted system score. Verify at the results
  portal: https://mlcommons.org/benchmarks/inference-datacenter/
- kind="illustrative": NOT anchored to a specific verified result — scaled
  from architecture ratios or public vendor claims. The `derivation` states
  the reasoning. Use as order-of-magnitude defaults only.

All figures are batched *server/offline* throughput per GPU — total output
tokens/sec at high concurrency, the number the cost-per-token model needs —
NOT single-request decode speed, which is ~50-100x lower. Real throughput
swings with engine version, precision, batch size, and sequence lengths.
"""

from __future__ import annotations

from dataclasses import dataclass

DATA_VINTAGE = "2026-06"
MLPERF_PORTAL = "https://mlcommons.org/benchmarks/inference-datacenter/"


@dataclass(frozen=True)
class ThroughputEntry:
    """Per-GPU batched inference throughput for one model on one GPU."""

    gpu: str  # canonical: H100 | H200 | B200
    model: str
    precision: str
    tokens_per_sec: float  # per-GPU, batched server/offline throughput
    kind: str  # "mlperf" | "illustrative"
    derivation: str  # how the number was obtained, stated plainly

    def __post_init__(self) -> None:
        if self.tokens_per_sec <= 0:
            raise ValueError("tokens_per_sec must be positive")
        if self.kind not in ("mlperf", "illustrative"):
            raise ValueError("kind must be 'mlperf' or 'illustrative'")


BENCHMARKS: tuple[ThroughputEntry, ...] = (
    # --- Llama 2 70B: the MLPerf Inference LLM workload with the deepest
    # public result set (closed division, offline scenario, FP8). ---
    ThroughputEntry(
        "H100", "llama-2-70b", "fp8", 3_000, "mlperf",
        "MLPerf Inference v4.1 closed/offline: 8xH100-SXM submissions cluster "
        "near ~24,000 tok/s per node; 24,000 / 8 = 3,000 per GPU.",
    ),
    ThroughputEntry(
        "H200", "llama-2-70b", "fp8", 3_800, "mlperf",
        "MLPerf Inference v4.1 closed/offline: 8xH200 submissions ~30,000-31,000 "
        "tok/s per node; ~30,400 / 8 = 3,800 per GPU.",
    ),
    ThroughputEntry(
        "B200", "llama-2-70b", "fp8", 11_200, "mlperf",
        "MLPerf Inference v5.0 closed/offline: 8xB200 submissions ~3x the 8xH200 "
        "per-node score; 3,800 x ~2.95 ≈ 11,200 per GPU.",
    ),
    # --- Llama 3.1 8B: no dense public MLPerf coverage at single-GPU
    # granularity; scaled from 70B results by parameter/bandwidth ratio. ---
    ThroughputEntry(
        "H100", "llama-3.1-8b", "fp8", 12_000, "illustrative",
        "Scaled ~4x from the H100 Llama-2-70B MLPerf figure: 8B has ~9x fewer "
        "params but batching efficiency saturates memory bandwidth first.",
    ),
    ThroughputEntry(
        "H200", "llama-3.1-8b", "fp8", 15_000, "illustrative",
        "H100 8B figure x 1.25: small models gain less from H200's extra HBM "
        "bandwidth than 70B (decode less bandwidth-bound per token).",
    ),
    ThroughputEntry(
        "B200", "llama-3.1-8b", "fp8", 28_000, "illustrative",
        "H100 8B figure x ~2.3, tracking the B200/H100 ratio observed in "
        "MLPerf 70B results and NVIDIA Blackwell launch claims.",
    ),
)

MODEL_LABELS = {
    "llama-2-70b": "Llama 2 70B (FP8, MLPerf-anchored, per-GPU of 8x node)",
    "llama-3.1-8b": "Llama 3.1 8B (FP8, illustrative)",
}


def models() -> list[str]:
    """Model ids with at least one benchmark entry, insertion-ordered."""
    seen: dict[str, None] = {}
    for e in BENCHMARKS:
        seen.setdefault(e.model, None)
    return list(seen)


def throughput(gpu: str, model: str) -> ThroughputEntry | None:
    """The benchmark entry for a (gpu, model) pair, or None if not covered."""
    for e in BENCHMARKS:
        if e.gpu == gpu and e.model == model:
            return e
    return None


def table() -> dict[str, object]:
    """JSON-shaped export: vintage, labels, and entries grouped by model."""
    by_model: dict[str, list[dict[str, object]]] = {m: [] for m in models()}
    for e in BENCHMARKS:
        by_model[e.model].append(
            {
                "gpu": e.gpu,
                "precision": e.precision,
                "tokens_per_sec": e.tokens_per_sec,
                "kind": e.kind,
                "derivation": e.derivation,
            }
        )
    return {
        "vintage": DATA_VINTAGE,
        "mlperf_portal": MLPERF_PORTAL,
        "note": (
            "Batched server/offline throughput per GPU. 'mlperf' rows derive "
            "from verified MLCommons closed-division results (see derivation); "
            "'illustrative' rows are scaled estimates, not measurements."
        ),
        "labels": MODEL_LABELS,
        "models": by_model,
    }
