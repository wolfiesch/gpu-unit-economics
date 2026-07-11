"""Auditable per-accelerator inference benchmark registry."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass

from gpu_econ.registry import (
    CLASSIFICATIONS,
    HARDWARE,
    MODELS,
    REGISTRY_DIR,
    REGISTRY_VERSION,
    SOURCES,
)

DATA_VINTAGE = "2026-07"
MLPERF_PORTAL = "https://mlcommons.org/benchmarks/inference-datacenter/"


@dataclass(frozen=True)
class ThroughputEntry:
    """One benchmark result normalized to output tokens/sec per accelerator."""

    gpu: str
    model: str
    precision: str
    tokens_per_sec: float
    classification: str
    derivation: str
    engine: str = "unknown"
    input_tokens: int | None = None
    output_tokens: int | None = None
    concurrency: int | None = None
    gpu_count: int = 1
    scenario: str = "server"
    tokens_per_sec_low: float | None = None
    tokens_per_sec_high: float | None = None
    confidence: str = "low"
    source_id: str = ""
    benchmark_date: str = ""

    def __post_init__(self) -> None:
        if self.tokens_per_sec <= 0:
            raise ValueError("tokens_per_sec must be positive")
        if self.classification not in CLASSIFICATIONS[:-1]:
            raise ValueError("classification must be measured, vendor-reported, or estimated")
        low = self.tokens_per_sec_low or self.tokens_per_sec
        high = self.tokens_per_sec_high or self.tokens_per_sec
        if not low <= self.tokens_per_sec <= high:
            raise ValueError("tokens_per_sec must fall inside its low/high range")

    @property
    def kind(self) -> str:
        """Backward-compatible name used by older API clients."""
        if self.classification == "measured":
            return "mlperf"
        if self.classification == "estimated":
            return "illustrative"
        return "vendor-reported"


def _optional_int(value: str) -> int | None:
    return int(value) if value.strip() else None


def _load() -> tuple[ThroughputEntry, ...]:
    with (REGISTRY_DIR / "benchmarks.csv").open(newline="", encoding="utf-8") as handle:
        rows = csv.DictReader(handle)
        return tuple(
            ThroughputEntry(
                gpu=row["hardware_id"],
                model=row["model_id"],
                precision=row["precision"],
                tokens_per_sec=float(row["tokens_per_sec"]),
                classification=row["classification"],
                derivation=row["derivation"],
                engine=row["engine"],
                input_tokens=_optional_int(row["input_tokens"]),
                output_tokens=_optional_int(row["output_tokens"]),
                concurrency=_optional_int(row["concurrency"]),
                gpu_count=int(row["gpu_count"]),
                scenario=row["scenario"],
                tokens_per_sec_low=float(row["tokens_per_sec_low"]),
                tokens_per_sec_high=float(row["tokens_per_sec_high"]),
                confidence=row["confidence"],
                source_id=row["source_id"],
                benchmark_date=row["benchmark_date"],
            )
            for row in rows
        )


BENCHMARKS = _load()
MODEL_LABELS = {model.id: model.label for model in MODELS.values()}


def models() -> list[str]:
    """All registered model ids, including models with evidence gaps."""
    return list(MODELS)


def throughput(gpu: str, model: str) -> ThroughputEntry | None:
    """Best benchmark entry for an exact hardware/model pair."""
    matches = [entry for entry in BENCHMARKS if entry.gpu == gpu and entry.model == model]
    if not matches:
        return None
    rank = {"measured": 3, "vendor-reported": 2, "estimated": 1}
    return max(matches, key=lambda entry: (rank[entry.classification], entry.benchmark_date))


def _export(entry: ThroughputEntry) -> dict[str, object]:
    row = asdict(entry)
    row["kind"] = entry.kind
    source = SOURCES[entry.source_id]
    row["source"] = asdict(source)
    row["hardware"] = asdict(HARDWARE[entry.gpu])
    return row


def table() -> dict[str, object]:
    """JSON-shaped benchmark export with evidence and source metadata."""
    entries = [_export(entry) for entry in BENCHMARKS]
    by_model = {
        model_id: [row for row in entries if row["model"] == model_id] for model_id in models()
    }
    pairs = {(entry.gpu, entry.model) for entry in BENCHMARKS}
    comparable_hardware = [item for item in HARDWARE.values() if item.product_type == "gpu"]
    coverage = [
        {
            "gpu": hardware.id,
            "model": model_id,
            "status": "covered" if (hardware.id, model_id) in pairs else "unavailable",
        }
        for model_id in models()
        for hardware in comparable_hardware
    ]
    return {
        "registry_version": REGISTRY_VERSION,
        "vintage": DATA_VINTAGE,
        "mlperf_portal": MLPERF_PORTAL,
        "classifications": CLASSIFICATIONS,
        "note": (
            "Throughput is normalized per accelerator. Measured and vendor-reported "
            "rows preserve their test setup; estimates always include a range. Missing "
            "hardware/model pairs remain unavailable."
        ),
        "labels": MODEL_LABELS,
        "models": by_model,
        "entries": entries,
        "hardware": [asdict(item) for item in HARDWARE.values()],
        "sources": [asdict(item) for item in SOURCES.values()],
        "coverage": coverage,
    }
