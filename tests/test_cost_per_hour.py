from dataclasses import FrozenInstanceError

import pytest

from gpu_econ.cost_per_hour import HourlyCost, cost_breakdown, cost_per_hour
from gpu_econ.inputs import (
    H100,
    HOURS_PER_YEAR,
    DataCenterAssumptions,
    GPUSpec,
    Scenario,
    WorkloadAssumptions,
)


def test_h100_default_cost_components_are_hand_computed() -> None:
    result = cost_per_hour(Scenario(gpu=H100))

    depreciation = 30_000 * 0.9 / 4 / 8_760
    power = 0.70 * 1.3 * 0.08
    opex = 30_000 * 0.05 / 8_760
    total = depreciation + power + opex

    assert result.depreciation_per_hour == pytest.approx(depreciation)
    assert result.power_per_hour == pytest.approx(power)
    assert result.opex_per_hour == pytest.approx(opex)
    assert result.total_per_provisioned_hour == pytest.approx(total)
    assert result.total_per_billable_hour == pytest.approx(total / 0.70)


def test_billable_cost_exceeds_provisioned_cost_below_full_utilization() -> None:
    workload = WorkloadAssumptions(utilization=0.5)
    result = cost_per_hour(Scenario(gpu=H100, workload=workload))

    assert result.total_per_billable_hour > result.total_per_provisioned_hour
    assert result.total_per_billable_hour == pytest.approx(
        result.total_per_provisioned_hour / 0.5
    )


def test_billable_cost_equals_provisioned_cost_at_full_utilization() -> None:
    workload = WorkloadAssumptions(utilization=1.0)
    result = cost_per_hour(Scenario(gpu=H100, workload=workload))

    assert result.total_per_billable_hour == pytest.approx(
        result.total_per_provisioned_hour
    )


def test_custom_gpu_depreciation_uses_residual_value_and_life() -> None:
    gpu = GPUSpec(
        name="Custom",
        capex_usd=10_000,
        power_kw=1.0,
        tokens_per_sec=1_000,
        useful_life_years=5.0,
        residual_value_frac=0.2,
    )

    result = cost_per_hour(Scenario(gpu=gpu))

    assert result.depreciation_per_hour == pytest.approx(
        10_000 * 0.8 / 5 / HOURS_PER_YEAR
    )


def test_power_cost_uses_pue_to_scale_board_power() -> None:
    gpu = GPUSpec(
        name="PowerTest",
        capex_usd=1_000,
        power_kw=2.0,
        tokens_per_sec=1_000,
    )
    datacenter = DataCenterAssumptions(power_cost_per_kwh=0.10, pue=1.5)

    result = cost_per_hour(Scenario(gpu=gpu, datacenter=datacenter))

    assert result.power_per_hour == pytest.approx(2.0 * 1.5 * 0.10)


def test_opex_cost_uses_capex_fraction_per_year() -> None:
    gpu = GPUSpec(
        name="OpexTest",
        capex_usd=12_000,
        power_kw=1.0,
        tokens_per_sec=1_000,
    )
    datacenter = DataCenterAssumptions(opex_frac_of_capex_per_year=0.06)

    result = cost_per_hour(Scenario(gpu=gpu, datacenter=datacenter))

    assert result.opex_per_hour == pytest.approx(12_000 * 0.06 / HOURS_PER_YEAR)


def test_cost_breakdown_returns_named_components() -> None:
    result = cost_per_hour(Scenario(gpu=H100))
    breakdown = cost_breakdown(Scenario(gpu=H100))

    assert breakdown == {
        "depreciation_per_hour": result.depreciation_per_hour,
        "power_per_hour": result.power_per_hour,
        "opex_per_hour": result.opex_per_hour,
        "total_per_provisioned_hour": result.total_per_provisioned_hour,
        "total_per_billable_hour": result.total_per_billable_hour,
    }


def test_hourly_cost_is_frozen() -> None:
    result = cost_per_hour(Scenario(gpu=H100))

    with pytest.raises(FrozenInstanceError):
        result.total_per_billable_hour = 0.0


def test_cost_per_hour_returns_hourly_cost_dataclass() -> None:
    result = cost_per_hour(Scenario(gpu=H100))

    assert isinstance(result, HourlyCost)
