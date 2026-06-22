from __future__ import annotations

import pytest

from gpu_econ.inputs import H100, HOURS_PER_YEAR, Scenario, WorkloadAssumptions
from gpu_econ.reserved_vs_spot import BreakEven, break_even, break_even_curve


def scenario_with_workload(workload: WorkloadAssumptions) -> Scenario:
    return Scenario(gpu=H100, workload=workload)


def test_default_break_even_matches_buyer_economics() -> None:
    result = break_even(Scenario(gpu=H100))

    assert isinstance(result, BreakEven)
    assert result.break_even_utilization == pytest.approx(0.64)
    assert result.term_hours == pytest.approx(8760.0)
    assert result.reserved_total_cost == pytest.approx(14016.0)
    assert result.on_demand_total_cost == pytest.approx(15330.0)
    assert result.cheaper_option == "reserved"
    assert result.savings_of_cheaper == pytest.approx(1314.0)


def test_on_demand_wins_below_break_even_utilization() -> None:
    scenario = scenario_with_workload(WorkloadAssumptions(utilization=0.50))

    result = break_even(scenario)

    assert result.break_even_utilization == pytest.approx(0.64)
    assert result.reserved_total_cost == pytest.approx(14016.0)
    assert result.on_demand_total_cost == pytest.approx(10950.0)
    assert result.cheaper_option == "on_demand"
    assert result.savings_of_cheaper == pytest.approx(3066.0)


def test_reserved_wins_above_break_even_utilization() -> None:
    scenario = scenario_with_workload(WorkloadAssumptions(utilization=0.90))

    result = break_even(scenario)

    assert result.reserved_total_cost == pytest.approx(14016.0)
    assert result.on_demand_total_cost == pytest.approx(19710.0)
    assert result.cheaper_option == "reserved"
    assert result.savings_of_cheaper == pytest.approx(5694.0)


def test_exact_break_even_has_equal_totals_and_zero_savings() -> None:
    scenario = scenario_with_workload(WorkloadAssumptions(utilization=0.64))

    result = break_even(scenario)

    assert result.reserved_total_cost == pytest.approx(result.on_demand_total_cost)
    assert result.savings_of_cheaper == pytest.approx(0.0)


def test_term_hours_scale_with_reserved_term_months() -> None:
    workload = WorkloadAssumptions(utilization=0.70, reserved_term_months=6)
    scenario = scenario_with_workload(workload)

    result = break_even(scenario)

    assert result.term_hours == pytest.approx(HOURS_PER_YEAR / 2)
    assert result.reserved_total_cost == pytest.approx(7008.0)
    assert result.on_demand_total_cost == pytest.approx(7665.0)


def test_break_even_uses_custom_prices() -> None:
    scenario = scenario_with_workload(
        WorkloadAssumptions(
            utilization=0.75,
            reserved_price_per_gpu_hour=1.20,
            on_demand_price_per_gpu_hour=3.00,
        )
    )

    result = break_even(scenario)

    assert result.break_even_utilization == pytest.approx(0.40)
    assert result.reserved_total_cost == pytest.approx(10512.0)
    assert result.on_demand_total_cost == pytest.approx(19710.0)
    assert result.cheaper_option == "reserved"
    assert result.savings_of_cheaper == pytest.approx(9198.0)


def test_break_even_curve_returns_chart_ready_rows_for_each_utilization() -> None:
    rows = break_even_curve(Scenario(gpu=H100), (0.50, 0.64, 0.70))

    expected = [
        {
            "utilization": 0.50,
            "reserved_total_cost": 14016.0,
            "on_demand_total_cost": 10950.0,
            "savings_from_reserving": -3066.0,
        },
        {
            "utilization": 0.64,
            "reserved_total_cost": 14016.0,
            "on_demand_total_cost": 14016.0,
            "savings_from_reserving": 0.0,
        },
        {
            "utilization": 0.70,
            "reserved_total_cost": 14016.0,
            "on_demand_total_cost": 15330.0,
            "savings_from_reserving": 1314.0,
        },
    ]

    assert len(rows) == len(expected)
    for row, expected_row in zip(rows, expected, strict=True):
        assert row.keys() == expected_row.keys()
        for key, expected_value in expected_row.items():
            assert row[key] == pytest.approx(expected_value)


def test_zero_reserved_price_breaks_even_at_zero_utilization() -> None:
    scenario = scenario_with_workload(
        WorkloadAssumptions(utilization=0.01, reserved_price_per_gpu_hour=0.0)
    )

    result = break_even(scenario)

    assert result.break_even_utilization == pytest.approx(0.0)
    assert result.reserved_total_cost == pytest.approx(0.0)
    assert result.cheaper_option == "reserved"
    assert result.savings_of_cheaper == pytest.approx(219.0)
