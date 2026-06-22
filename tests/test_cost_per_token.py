from dataclasses import FrozenInstanceError

import pytest

from gpu_econ.cost_per_token import TokenCost, compare_gpus, cost_per_million_tokens
from gpu_econ.inputs import (
    B200,
    DEFAULT_GPUS,
    H100,
    DataCenterAssumptions,
    GPUSpec,
    Scenario,
    WorkloadAssumptions,
)


def test_h100_default_cost_per_million_tokens_matches_shared_formula() -> None:
    result = cost_per_million_tokens(Scenario(gpu=H100))

    assert result.gpu_name == "H100"
    assert result.effective_tokens_per_hour == pytest.approx(6_300_000.0)
    assert result.cost_per_provisioned_hour == pytest.approx(1.014580821917808)
    assert result.cost_per_million_tokens == pytest.approx(0.16104457490758858)


def test_effective_tokens_per_hour_scales_by_utilized_seconds() -> None:
    gpu = GPUSpec(name="TestGPU", capex_usd=8_760, power_kw=1.0, tokens_per_sec=1_000)
    workload = WorkloadAssumptions(utilization=0.25)

    result = cost_per_million_tokens(Scenario(gpu=gpu, workload=workload))

    assert result.effective_tokens_per_hour == pytest.approx(900_000.0)


def test_cost_per_million_tokens_uses_cost_per_provisioned_hour_not_billable_hour() -> None:
    gpu = GPUSpec(
        name="NoPower",
        capex_usd=8_760,
        power_kw=1.0,
        tokens_per_sec=1_000,
        useful_life_years=1.0,
        residual_value_frac=0.0,
    )
    datacenter = DataCenterAssumptions(
        power_cost_per_kwh=0.0,
        pue=2.0,
        opex_frac_of_capex_per_year=0.0,
    )
    workload = WorkloadAssumptions(utilization=0.50)

    result = cost_per_million_tokens(Scenario(gpu=gpu, datacenter=datacenter, workload=workload))

    assert result.cost_per_provisioned_hour == pytest.approx(1.0)
    assert result.effective_tokens_per_hour == pytest.approx(1_800_000.0)
    assert result.cost_per_million_tokens == pytest.approx(1.0 / 1.8)


def test_compare_gpus_defaults_to_default_gpu_set_sorted_by_token_cost() -> None:
    results = compare_gpus(None)

    assert len(results) == 3
    assert {result.gpu_name for result in results} == {gpu.name for gpu in DEFAULT_GPUS}
    assert [result.cost_per_million_tokens for result in results] == sorted(
        result.cost_per_million_tokens for result in results
    )


def test_b200_beats_h100_on_default_cost_per_million_tokens() -> None:
    results_by_name = {
        result.gpu_name: result for result in compare_gpus([H100, B200])
    }

    assert (
        results_by_name["B200"].cost_per_million_tokens
        < results_by_name["H100"].cost_per_million_tokens
    )


def test_compare_gpus_reuses_custom_datacenter_and_workload_assumptions() -> None:
    gpu = GPUSpec(
        name="LeanGPU",
        capex_usd=8_760,
        power_kw=1.0,
        tokens_per_sec=1_000,
        useful_life_years=1.0,
        residual_value_frac=0.0,
    )
    datacenter = DataCenterAssumptions(
        power_cost_per_kwh=0.25,
        pue=2.0,
        opex_frac_of_capex_per_year=0.0,
    )
    workload = WorkloadAssumptions(utilization=1.0)

    result = compare_gpus([gpu], datacenter=datacenter, workload=workload)[0]

    assert result.effective_tokens_per_hour == pytest.approx(3_600_000.0)
    assert result.cost_per_provisioned_hour == pytest.approx(1.5)
    assert result.cost_per_million_tokens == pytest.approx(1.5 / 3.6)


def test_compare_gpus_accepts_generators() -> None:
    gpus = (gpu for gpu in [H100, B200])

    results = compare_gpus(gpus)

    assert [result.gpu_name for result in results] == ["B200", "H100"]


def test_compare_gpus_returns_empty_list_for_empty_iterable() -> None:
    assert compare_gpus([]) == []


def test_token_cost_result_is_frozen() -> None:
    result = TokenCost(
        gpu_name="FrozenGPU",
        effective_tokens_per_hour=1.0,
        cost_per_provisioned_hour=2.0,
        cost_per_million_tokens=3.0,
    )

    with pytest.raises(FrozenInstanceError):
        result.cost_per_million_tokens = 4.0
