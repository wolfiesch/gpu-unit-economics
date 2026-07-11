import pytest

from gpu_econ.workloads import GPU_MEMORY_GB, PROFILES, WorkloadProfile, catalog, evaluate


def test_catalog_covers_interactive_batch_long_context_code_and_reasoning() -> None:
    rows = catalog()

    assert [row["id"] for row in rows] == [
        "interactive",
        "batch",
        "long-context",
        "code-batch",
        "reasoning",
        "latest-chat",
        "latest-code",
        "latest-frontier",
    ]
    assert all(row["model"] and row["input_tokens"] > 0 for row in rows)


@pytest.mark.parametrize("workload", PROFILES)
def test_evaluate_returns_explicit_result_or_unavailable_for_each_gpu(workload: str) -> None:
    results = evaluate(workload)

    assert [result.gpu for result in results] == list(GPU_MEMORY_GB)
    for result in results:
        if result.effective_tokens_per_sec is None:
            assert result.provenance == "unavailable"
            assert result.confidence == "none"
            assert result.benchmark_classification == "unavailable"
            assert result.performance_evidence_available is False
        else:
            assert result.effective_tokens_per_sec > 0
            assert result.effective_tokens_per_sec_low <= result.effective_tokens_per_sec
            assert result.effective_tokens_per_sec_high >= result.effective_tokens_per_sec
            assert result.provenance in {"measured", "vendor-reported", "estimated"}
            assert result.confidence in {"high", "medium", "low"}
        assert result.reason == ("; ".join(result.reasons) or None)
        assert result.fleet_sizing_inputs.tokens_per_sec == (result.effective_tokens_per_sec or 0)


def test_effective_throughput_is_workload_adjusted() -> None:
    interactive = {row.gpu: row for row in evaluate("interactive")}
    batch = {row.gpu: row for row in evaluate("batch")}

    assert interactive["H100"].effective_tokens_per_sec == 6_372 * 0.32
    assert batch["H100"].effective_tokens_per_sec == 2_975 * 0.90


def test_memory_context_and_latency_failures_are_human_readable() -> None:
    profile = WorkloadProfile(
        "fixture",
        "Fixture",
        "llama-3.1-8b",
        1_000,
        200_000,
        100,
        0.01,
        0.5,
        0.5,
        0.5,
        0.1,
    )

    result = next(row for row in evaluate(profile) if row.gpu == "H100")

    assert not result.compatible
    assert "registered model limit" in result.reason
    assert "Needs about" in result.reason
    assert "latency exceeds" in result.reason


def test_unknown_workload_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown workload"):
        evaluate("not-a-profile")


def test_latest_model_can_fit_memory_without_claiming_speed_evidence() -> None:
    result = next(row for row in evaluate("latest-chat") if row.gpu == "H100")

    assert result.compatible is True
    assert result.performance_evidence_available is False
    assert result.effective_tokens_per_sec is None
    assert result.reason is None


def test_vendor_results_have_more_confidence_than_estimates() -> None:
    batch = {row.gpu: row for row in evaluate("batch")}

    assert batch["H100"].provenance == "vendor-reported"
    assert batch["H100"].confidence == "medium"
    assert batch["MI325X"].provenance == "estimated"
    assert batch["MI325X"].confidence == "low"
