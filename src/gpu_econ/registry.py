"""Versioned hardware, model, benchmark, and source registries.

The CSV files are deliberately plain: reviewers can audit them without running
Python, while the application gets one typed source of truth.
"""

from __future__ import annotations

import csv
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

REGISTRY_VERSION = "2026.07.3"
CLASSIFICATIONS = ("measured", "vendor-reported", "estimated", "unavailable")


def _registry_dir(module_file: Path, working_directory: Path) -> Path:
    """Find registry CSVs in source-tree and installed-container layouts."""
    configured = os.environ.get("GPU_ECON_REGISTRY_DIR")
    candidates = [
        Path(configured) if configured else None,
        module_file.resolve().parents[2] / "data" / "registry",
        working_directory.resolve() / "data" / "registry",
    ]
    for candidate in candidates:
        if candidate is not None and (candidate / "sources.csv").is_file():
            return candidate
    searched = ", ".join(str(candidate) for candidate in candidates if candidate is not None)
    raise FileNotFoundError(f"GPU registry not found; searched: {searched}")


REGISTRY_DIR = _registry_dir(Path(__file__), Path.cwd())


def _float(value: str) -> float | None:
    return float(value) if value.strip() else None


def _int(value: str) -> int | None:
    return int(value) if value.strip() else None


def _rows(name: str, record_type: type[object]) -> list[dict[str, str]]:
    with (REGISTRY_DIR / name).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {field.name for field in fields(record_type)}
        missing = sorted(required - set(reader.fieldnames or ()))
        if missing:
            raise ValueError(
                f"{name} is missing required columns for registry {REGISTRY_VERSION}: "
                f"{', '.join(missing)}"
            )
        return list(reader)


@dataclass(frozen=True)
class SourceRecord:
    id: str
    publisher: str
    title: str
    url: str
    published_date: str
    source_type: str


@dataclass(frozen=True)
class HardwareRecord:
    id: str
    display_name: str
    vendor: str
    product_type: str
    architecture: str
    memory_gb: float
    power_w: float | None
    form_factor: str
    interconnect: str
    accelerator_count: int
    ownership_supported: bool
    capex_usd: float | None
    capex_confidence: str
    capex_source_id: str
    spec_source_id: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class ModelRecord:
    id: str
    label: str
    family: str
    parameters_b: float
    active_parameters_b: float
    architecture: str
    weights_gb: float
    weight_precision: str
    runtime_overhead_gb_per_gpu: float
    kv_cache_gb_per_1k_tokens: float
    tensor_parallel_gpus: int
    max_context_tokens: int
    released_date: str
    source_id: str


SOURCES = {row["id"]: SourceRecord(**row) for row in _rows("sources.csv", SourceRecord)}

HARDWARE = {
    row["id"]: HardwareRecord(
        id=row["id"],
        display_name=row["display_name"],
        vendor=row["vendor"],
        product_type=row["product_type"],
        architecture=row["architecture"],
        memory_gb=float(row["memory_gb"]),
        power_w=_float(row["power_w"]),
        form_factor=row["form_factor"],
        interconnect=row["interconnect"],
        accelerator_count=int(row["accelerator_count"]),
        ownership_supported=row["ownership_supported"].lower() == "true",
        capex_usd=_float(row["capex_usd"]),
        capex_confidence=row["capex_confidence"],
        capex_source_id=row["capex_source_id"],
        spec_source_id=row["spec_source_id"],
        aliases=tuple(alias.strip() for alias in row["aliases"].split("|") if alias.strip()),
    )
    for row in _rows("hardware.csv", HardwareRecord)
}

MODELS = {
    row["id"]: ModelRecord(
        id=row["id"],
        label=row["label"],
        family=row["family"],
        parameters_b=float(row["parameters_b"]),
        active_parameters_b=float(row["active_parameters_b"]),
        architecture=row["architecture"],
        weights_gb=float(row["weights_gb"]),
        weight_precision=row["weight_precision"],
        runtime_overhead_gb_per_gpu=float(row["runtime_overhead_gb_per_gpu"]),
        kv_cache_gb_per_1k_tokens=float(row["kv_cache_gb_per_1k_tokens"]),
        tensor_parallel_gpus=int(row["tensor_parallel_gpus"]),
        max_context_tokens=int(row["max_context_tokens"]),
        released_date=row["released_date"],
        source_id=row["source_id"],
    )
    for row in _rows("models.csv", ModelRecord)
}


def _validate() -> None:
    for item in HARDWARE.values():
        if item.spec_source_id not in SOURCES:
            raise ValueError(f"unknown hardware source {item.spec_source_id!r}")
        if item.capex_source_id and item.capex_source_id not in SOURCES:
            raise ValueError(f"unknown capex source {item.capex_source_id!r}")
        if item.ownership_supported and (item.capex_usd is None or not item.capex_source_id):
            raise ValueError(f"ownership-supported hardware {item.id!r} needs sourced capex")
    for item in MODELS.values():
        if item.source_id not in SOURCES:
            raise ValueError(f"unknown model source {item.source_id!r}")


_validate()


def catalog() -> dict[str, object]:
    """Return the complete registry with source records expanded."""
    hardware = []
    for item in HARDWARE.values():
        row = asdict(item)
        row["source"] = asdict(SOURCES[item.spec_source_id])
        row["capex_source"] = (
            asdict(SOURCES[item.capex_source_id]) if item.capex_source_id else None
        )
        hardware.append(row)
    models = []
    for item in MODELS.values():
        row = asdict(item)
        row["source"] = asdict(SOURCES[item.source_id])
        models.append(row)
    return {
        "registry_version": REGISTRY_VERSION,
        "classifications": CLASSIFICATIONS,
        "hardware": hardware,
        "models": models,
        "sources": [asdict(source) for source in SOURCES.values()],
    }
