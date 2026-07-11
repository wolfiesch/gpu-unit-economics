import pytest

from gpu_econ.fleet_sizing import HOURS_PER_MONTH, size_fleet
from gpu_econ.inputs import H100, Scenario


def test_size_fleet_reserves_headroom_and_rounds_up_capacity() -> None:
    scenario = Scenario(gpu=H100)
    usable_tokens_per_gpu = 2_500 * 3_600 * HOURS_PER_MONTH * 0.70 * 0.85
    demand = usable_tokens_per_gpu * 2.2

    result = size_fleet(scenario, demand, rental_price_per_hour=2.0)

    assert result.fleet_size == 3
    assert result.monthly_token_capacity == pytest.approx(usable_tokens_per_gpu * 3)
    assert result.capacity_coverage == pytest.approx(3 / 2.2)
    assert result.upfront_capex == 90_000


def test_renting_scales_with_exact_active_hours_while_ownership_uses_full_fleet() -> None:
    scenario = Scenario(gpu=H100)
    demand = 18_000_000_000

    result = size_fleet(
        scenario,
        monthly_token_demand=demand,
        rental_price_per_hour=1.50,
        capacity_headroom=0.10,
        horizon_months=24,
    )

    expected_active_hours = demand / (2_500 * 3_600)
    assert result.active_gpu_hours_per_month == pytest.approx(expected_active_hours)
    assert result.monthly_rental_cost == pytest.approx(expected_active_hours * 1.50)
    assert result.own_total_cost == pytest.approx(result.monthly_ownership_cost * 24)
    assert result.rent_total_cost == pytest.approx(result.monthly_rental_cost * 24)
    assert result.cheaper_option in {"own", "rent"}
    assert result.savings_of_cheaper == pytest.approx(
        abs(result.own_total_cost - result.rent_total_cost)
    )


@pytest.mark.parametrize(
    ("demand", "rental_price", "headroom", "horizon", "message"),
    [
        (0, 1.0, 0.15, 36, "monthly_token_demand must be positive"),
        (1, -0.01, 0.15, 36, "rental_price_per_hour must be non-negative"),
        (1, 1.0, -0.01, 36, "capacity_headroom must be in \\[0, 1\\)"),
        (1, 1.0, 1.0, 36, "capacity_headroom must be in \\[0, 1\\)"),
        (1, 1.0, 0.15, 0, "horizon_months must be positive"),
    ],
)
def test_size_fleet_validates_inputs(
    demand: float,
    rental_price: float,
    headroom: float,
    horizon: float,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        size_fleet(
            Scenario(gpu=H100),
            monthly_token_demand=demand,
            rental_price_per_hour=rental_price,
            capacity_headroom=headroom,
            horizon_months=horizon,
        )
