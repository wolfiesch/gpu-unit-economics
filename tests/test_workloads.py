import pytest

from gpu_econ.workloads import (
    GPU_MEMORY_GB,
    PROFILES,
    WorkloadProfile,
    catalog,
    evaluate,
)


def test_catalog_has_three_curated_workloads() -> None:
    rows = catalog()

    assert [row["id"] for row in rows] == ["interactive", "batch", "long-context"]
    assert all(row["model"] and row["input_tokens"] > 0 for row in rows)


@pytest.mark.parametrize("workload", PROFILES)
def test_evaluate_returns_serializable_fields_for_each_gpu(workload: str) -> None:
    results = evaluate(workload)

    assert [result.gpu for result in results] == list(GPU_MEMORY_GB)
    for result in results:
        assert result.effective_tokens_per_sec > 0
        assert result.confidence in {"medium", "low"}
        assert result.provenance in {"measured", "estimated"}
        assert result.benchmark_kind in {"mlperf", "illustrative"}
        assert result.reason == ("; ".join(result.reasons) or None)
        assert result.fleet_sizing_inputs.tokens_per_sec == result.effective_tokens_per_sec


def test_effective_throughput_is_workload_adjusted() -> None:
    interactive = evaluate("interactive")
    batch = evaluate("batch")

    assert interactive[0].effective_tokens_per_sec == 12_000 * 0.32
    assert batch[0].effective_tokens_per_sec == 3_000 * 0.90


def test_memory_or_latency_failure_has_human_readable_reason() -> None:
    profile = WorkloadProfile(
        "fixture", "Fixture", "llama-3.1-8b", 1_000, 200_000, 100, 0.01,
        0.5, 0.5, 0.5, 0.1,
    )

    results = evaluate(profile)

    assert not results[0].compatible
    assert "Needs about" in results[0].reason
    assert "latency exceeds" in results[0].reason


def test_unknown_workload_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown workload"):
        evaluate("not-a-profile")


def test_batch_measured_data_has_more_confidence_than_estimated_data() -> None:
    batch = evaluate("batch")

    assert {row.provenance for row in batch[:2]} == {"measured"}
    assert {row.confidence for row in batch[:2]} == {"medium"}

