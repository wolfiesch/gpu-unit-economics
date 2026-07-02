import math

import pytest

from gpu_econ.cost_per_hour import cost_per_hour
from gpu_econ.inputs import H100, HOURS_PER_YEAR, Scenario
from gpu_econ.rent_vs_buy import rent_vs_buy, rent_vs_buy_curve


def h100_default_owner_rate() -> float:
    depreciation = 30_000 * 0.9 / 4 / 8_760
    power = 0.70 * 1.3 * 0.08
    opex = 30_000 * 0.05 / 8_760
    return depreciation + power + opex


def test_rent_vs_buy_uses_provisioned_owner_rate_and_utilized_rental_hours() -> None:
    scenario = Scenario(gpu=H100)
    rental_price = 1.20
    horizon_months = 36.0

    result = rent_vs_buy(
        scenario,
        rental_price_per_hour=rental_price,
        horizon_months=horizon_months,
    )

    owner_rate = h100_default_owner_rate()
    horizon_hours = 36.0 / 12 * HOURS_PER_YEAR

    assert result.owner_cost_per_provisioned_hour == pytest.approx(owner_rate)
    assert result.owner_cost_per_provisioned_hour == pytest.approx(
        cost_per_hour(scenario).total_per_provisioned_hour
    )
    assert result.break_even_utilization == pytest.approx(owner_rate / rental_price)
    assert result.horizon_hours == pytest.approx(horizon_hours)
    assert result.own_total_cost == pytest.approx(owner_rate * horizon_hours)
    assert result.rent_total_cost == pytest.approx(rental_price * horizon_hours * 0.70)
    assert result.cheaper_option == "rent"
    assert result.savings_of_cheaper == pytest.approx(
        owner_rate * horizon_hours - rental_price * horizon_hours * 0.70
    )


def test_cheaper_option_flips_around_the_utilization_adjusted_rental_price() -> None:
    scenario = Scenario(gpu=H100)
    owner_rate = h100_default_owner_rate()
    rental_price_where_totals_tie = owner_rate / 0.70

    rent_is_cheaper = rent_vs_buy(
        scenario,
        rental_price_per_hour=rental_price_where_totals_tie * 0.8,
        horizon_months=36.0,
    )
    own_is_cheaper = rent_vs_buy(
        scenario,
        rental_price_per_hour=rental_price_where_totals_tie * 1.2,
        horizon_months=36.0,
    )

    assert rent_is_cheaper.cheaper_option == "rent"
    assert rent_is_cheaper.rent_total_cost < rent_is_cheaper.own_total_cost
    assert own_is_cheaper.cheaper_option == "own"
    assert own_is_cheaper.own_total_cost < own_is_cheaper.rent_total_cost


def test_zero_rental_price_has_infinite_break_even_and_zero_rent_total() -> None:
    result = rent_vs_buy(
        Scenario(gpu=H100),
        rental_price_per_hour=0.0,
        horizon_months=36.0,
    )

    assert math.isinf(result.break_even_utilization)
    assert result.rent_total_cost == 0.0
    assert result.cheaper_option == "rent"
    assert result.savings_of_cheaper == pytest.approx(result.own_total_cost)


@pytest.mark.parametrize(
    ("rental_price", "horizon_months", "message"),
    [
        (-0.01, 36.0, "rental_price_per_hour must be non-negative"),
        (1.0, 0.0, "horizon_months must be positive"),
        (1.0, -1.0, "horizon_months must be positive"),
    ],
)
def test_rent_vs_buy_rejects_negative_prices_and_non_positive_horizons(
    rental_price: float,
    horizon_months: float,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        rent_vs_buy(
            Scenario(gpu=H100),
            rental_price_per_hour=rental_price,
            horizon_months=horizon_months,
        )


def test_curve_keeps_owning_cost_flat_and_rental_cost_linear_by_utilization() -> None:
    scenario = Scenario(gpu=H100)
    rental_price = 2.0
    horizon_months = 24.0
    utilizations = (0.25, 0.50, 0.75)

    rows = rent_vs_buy_curve(
        scenario,
        rental_price_per_hour=rental_price,
        utilizations=utilizations,
        horizon_months=horizon_months,
    )

    owner_rate = h100_default_owner_rate()
    horizon_hours = 24.0 / 12 * HOURS_PER_YEAR
    own_total = owner_rate * horizon_hours

    assert [row["utilization"] for row in rows] == list(utilizations)
    for row, utilization in zip(rows, utilizations, strict=True):
        rent_total = rental_price * horizon_hours * utilization
        assert row["own_total_cost"] == pytest.approx(own_total)
        assert row["rent_total_cost"] == pytest.approx(rent_total)
        assert row["savings_from_owning"] == pytest.approx(rent_total - own_total)
