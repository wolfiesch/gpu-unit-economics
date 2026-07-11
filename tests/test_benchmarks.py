import pytest

from gpu_econ import benchmarks
from gpu_econ.benchmarks import BENCHMARKS, ThroughputEntry, models, table, throughput
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
