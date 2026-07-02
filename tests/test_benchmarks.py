import pytest

from gpu_econ import benchmarks
from gpu_econ.benchmarks import BENCHMARKS, ThroughputEntry, models, table, throughput


@pytest.mark.parametrize("tokens_per_sec", [0, -1])
def test_throughput_entry_rejects_non_positive_tokens_per_sec(tokens_per_sec: float) -> None:
    with pytest.raises(ValueError, match="tokens_per_sec must be positive"):
        ThroughputEntry("H100", "llama-2-70b", "fp8", tokens_per_sec, "mlperf", "fixture")


def test_throughput_entry_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="kind must be 'mlperf' or 'illustrative'"):
        ThroughputEntry("H100", "llama-2-70b", "fp8", 1, "vendor-claim", "fixture")


def test_throughput_returns_exact_gpu_model_entry_or_none() -> None:
    expected = BENCHMARKS[1]

    assert throughput(expected.gpu, expected.model) is expected
    assert throughput("H100", "missing-model") is None
    assert throughput("missing-gpu", expected.model) is None


def test_models_returns_unique_ids_in_first_benchmark_order() -> None:
    expected: list[str] = []
    for entry in BENCHMARKS:
        if entry.model not in expected:
            expected.append(entry.model)

    assert models() == expected


def test_table_exports_grouped_models_with_metadata_and_provenance() -> None:
    exported = table()

    assert exported["vintage"] == benchmarks.DATA_VINTAGE
    assert exported["mlperf_portal"] == benchmarks.MLPERF_PORTAL
    assert exported["note"]
    assert set(exported["labels"]) == set(exported["models"]) == set(models())

    for model, rows in exported["models"].items():
        assert exported["labels"][model]
        assert rows == [
            {
                "gpu": entry.gpu,
                "precision": entry.precision,
                "tokens_per_sec": entry.tokens_per_sec,
                "kind": entry.kind,
                "derivation": entry.derivation,
            }
            for entry in BENCHMARKS
            if entry.model == model
        ]
        for row in rows:
            assert row["kind"] in {"mlperf", "illustrative"}
            assert row["derivation"]


def test_benchmarks_do_not_repeat_gpu_model_pairs() -> None:
    pairs = [(entry.gpu, entry.model) for entry in BENCHMARKS]

    assert len(pairs) == len(set(pairs))


def test_benchmarks_use_only_canonical_gpu_names() -> None:
    assert {entry.gpu for entry in BENCHMARKS} <= {"H100", "H200", "B200"}
