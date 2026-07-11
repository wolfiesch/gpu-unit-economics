from dataclasses import replace

import pytest

from gpu_econ import benchmarks
from gpu_econ.benchmarks import (
    BENCHMARKS,
    ThroughputEntry,
    _validate_benchmarks,
    models,
    table,
    throughput,
)
from gpu_econ.registry import CLASSIFICATIONS, HARDWARE, MODELS, REGISTRY_VERSION, SOURCES


@pytest.mark.parametrize("tokens_per_sec", [0, -1])
def test_throughput_entry_rejects_non_positive_tokens_per_sec(tokens_per_sec: float) -> None:
    with pytest.raises(ValueError, match="tokens_per_sec must be positive"):
        ThroughputEntry("H100", "llama-3.1-8b", "fp8", tokens_per_sec, "measured", "fixture")


def test_throughput_entry_rejects_unknown_classification() -> None:
    with pytest.raises(ValueError, match="classification must be"):
        ThroughputEntry("H100", "llama-3.1-8b", "fp8", 1, "vendor-claim", "fixture")


def test_throughput_entry_rejects_point_outside_range() -> None:
    with pytest.raises(ValueError, match="low/high range"):
        ThroughputEntry(
            "H100",
            "llama-3.1-8b",
            "fp8",
            100,
            "estimated",
            "fixture",
            tokens_per_sec_low=110,
            tokens_per_sec_high=120,
        )


def test_throughput_returns_best_exact_gpu_model_entry_or_none() -> None:
    expected = next(row for row in BENCHMARKS if row.gpu == "H100" and row.model == "llama-3.1-8b")

    assert throughput(expected.gpu, expected.model) is expected
    assert throughput("H100", "missing-model") is None
    assert throughput("missing-gpu", expected.model) is None


def test_models_returns_every_registered_model() -> None:
    assert models() == list(MODELS)


def test_table_exports_registry_metadata_sources_ranges_and_gaps() -> None:
    exported = table()

    assert exported["registry_version"] == REGISTRY_VERSION
    assert exported["vintage"] == benchmarks.DATA_VINTAGE
    assert exported["note"]
    assert set(exported["labels"]) == set(exported["models"]) == set(models())
    assert {row["id"] for row in exported["sources"]} == set(SOURCES)
    assert {row["id"] for row in exported["hardware"]} == set(HARDWARE)
    assert any(row["status"] == "unavailable" for row in exported["coverage"])

    for row in exported["entries"]:
        assert row["classification"] in CLASSIFICATIONS[:-1]
        assert row["tokens_per_sec_low"] <= row["tokens_per_sec"] <= row["tokens_per_sec_high"]
        assert row["source"]["url"].startswith("https://")
        assert row["derivation"]


def test_benchmarks_do_not_repeat_identical_test_configurations() -> None:
    keys = [
        (
            entry.gpu,
            entry.model,
            entry.engine,
            entry.precision,
            entry.scenario,
            entry.benchmark_date,
        )
        for entry in BENCHMARKS
    ]
    assert len(keys) == len(set(keys))


def test_benchmarks_reference_registered_hardware_models_and_sources() -> None:
    assert {entry.gpu for entry in BENCHMARKS} <= set(HARDWARE)
    assert {entry.model for entry in BENCHMARKS} <= set(MODELS)
    assert {entry.source_id for entry in BENCHMARKS} <= set(SOURCES)
    assert all(item.spec_source_id in SOURCES for item in HARDWARE.values())
    assert all(
        not item.ownership_supported or item.capex_source_id in SOURCES
        for item in HARDWARE.values()
    )
    assert {item.vendor for item in HARDWARE.values()} == {"NVIDIA", "AMD"}
    assert len(HARDWARE) >= 8


def test_coverage_batch_uses_published_l40s_and_mlperf_results() -> None:
    rows = {(entry.gpu, entry.model): entry for entry in BENCHMARKS}

    assert rows[("L40S", "llama-3.1-8b")].classification == "vendor-reported"
    assert rows[("L40S", "llama-3.1-8b")].tokens_per_sec == 3_134
    assert rows[("H200", "llama-3.1-8b")].classification == "measured"
    assert rows[("B200", "llama-3.1-8b")].tokens_per_sec == 18_370
    assert rows[("B200", "deepseek-r1-671b")].classification == "measured"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"gpu": "missing"}, "unknown or non-GPU hardware"),
        ({"model": "missing"}, "unknown model"),
        ({"source_id": "missing"}, "unknown benchmark source"),
        ({"gpu_count": 0}, "gpu_count must be positive"),
        ({"confidence": "certain"}, "unknown confidence"),
    ],
)
def test_benchmark_registry_rejects_invalid_references_and_shapes(
    change: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _validate_benchmarks((replace(BENCHMARKS[0], **change),))


def test_measured_rows_require_independent_sources() -> None:
    vendor_row = next(entry for entry in BENCHMARKS if entry.classification == "vendor-reported")

    with pytest.raises(ValueError, match="independent benchmark source"):
        _validate_benchmarks((replace(vendor_row, classification="measured"),))
