import pytest

from gpu_econ.inputs import H100, HOURS_PER_YEAR, Scenario
from gpu_econ.margin import MarginResult, gross_margin, margin_at_prices


def test_default_h100_on_demand_margin_uses_billable_cost_basis() -> None:
    result = gross_margin(Scenario(gpu=H100))

    assert result.price_per_billable_hour == pytest.approx(2.50)
    assert result.cost_per_billable_hour == pytest.approx(1.449398, abs=0.00001)
    assert result.gross_profit_per_billable_hour == pytest.approx(1.050602, abs=0.00001)
    assert result.gross_margin_pct == pytest.approx(0.420241, abs=0.00001)
    assert result.annual_gross_profit_per_gpu == pytest.approx(6442.3, abs=0.1)


def test_explicit_price_overrides_on_demand_price() -> None:
    result = gross_margin(Scenario(gpu=H100), price_per_billable_hour=3.00)

    assert result.price_per_billable_hour == pytest.approx(3.00)
    assert result.gross_profit_per_billable_hour == pytest.approx(
        3.00 - result.cost_per_billable_hour
    )


def test_price_equal_to_cost_has_zero_profit_and_margin() -> None:
    cost = gross_margin(Scenario(gpu=H100)).cost_per_billable_hour
    result = gross_margin(Scenario(gpu=H100), price_per_billable_hour=cost)

    assert result.gross_profit_per_billable_hour == pytest.approx(0.0)
    assert result.gross_margin_pct == pytest.approx(0.0)
    assert result.annual_gross_profit_per_gpu == pytest.approx(0.0)


def test_zero_price_returns_zero_margin_instead_of_dividing_by_zero() -> None:
    result = gross_margin(Scenario(gpu=H100), price_per_billable_hour=0.0)

    assert result.price_per_billable_hour == pytest.approx(0.0)
    assert result.gross_margin_pct == pytest.approx(0.0)
    assert result.gross_profit_per_billable_hour == pytest.approx(-result.cost_per_billable_hour)


def test_annual_gross_profit_scales_by_billable_hours_not_provisioned_hours() -> None:
    scenario = Scenario(gpu=H100)
    result = gross_margin(scenario, price_per_billable_hour=2.75)
    expected = (
        result.gross_profit_per_billable_hour
        * HOURS_PER_YEAR
        * scenario.workload.utilization
    )

    assert result.annual_gross_profit_per_gpu == pytest.approx(expected)


def test_margin_at_prices_returns_ordered_results_for_price_sweep() -> None:
    prices = [1.50, 2.50, 3.50]
    results = margin_at_prices(Scenario(gpu=H100), prices)

    assert [result.price_per_billable_hour for result in results] == prices
    assert all(isinstance(result, MarginResult) for result in results)


def test_margin_rises_as_price_rises() -> None:
    results = margin_at_prices(Scenario(gpu=H100), [2.00, 2.50, 3.00])
    margins = [result.gross_margin_pct for result in results]

    assert margins == sorted(margins)
    assert margins[0] < margins[1] < margins[2]
