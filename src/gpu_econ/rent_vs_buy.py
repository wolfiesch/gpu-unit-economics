"""Rent versus buy: at what utilization does owning a GPU beat renting it?

Owner cost per provisioned hour is fixed (depreciation + power + opex) no matter
the load; rental cost accrues only on utilized hours. So owning wins above the
utilization where `rental_rate * u` exceeds the owner's provisioned rate:

    u* = owner_cost_per_provisioned_hour / rental_price_per_hour

Below u* rent; above it buy. The horizon comparison prices a fixed number of
months both ways at the scenario's actual utilization.
"""

from __future__ import annotations

from dataclasses import dataclass

from gpu_econ.cost_per_hour import cost_per_hour
from gpu_econ.inputs import HOURS_PER_YEAR, Scenario


@dataclass(frozen=True)
class RentVsBuy:
    """Break-even utilization plus a fixed-horizon cost comparison."""

    rental_price_per_hour: float
    owner_cost_per_provisioned_hour: float
    break_even_utilization: float  # inf when renting is free
    horizon_months: float
    horizon_hours: float
    own_total_cost: float
    rent_total_cost: float
    cheaper_option: str  # "own" | "rent"
    savings_of_cheaper: float


def rent_vs_buy(
    scenario: Scenario,
    rental_price_per_hour: float,
    horizon_months: float = 36.0,
) -> RentVsBuy:
    """Compare owning (provisioned cost) vs renting (billable cost) over a horizon.

    `rental_price_per_hour` is what a cloud charges per utilized GPU-hour —
    pass a live market quote here. Ownership cost comes from the scenario's
    fully-loaded provisioned rate and is paid for every hour of the horizon.
    """
    if rental_price_per_hour < 0:
        raise ValueError("rental_price_per_hour must be non-negative")
    if horizon_months <= 0:
        raise ValueError("horizon_months must be positive")

    hourly = cost_per_hour(scenario)
    owner_rate = hourly.total_per_provisioned_hour
    utilization = scenario.workload.utilization

    horizon_hours = horizon_months / 12 * HOURS_PER_YEAR
    own_total_cost = owner_rate * horizon_hours
    rent_total_cost = rental_price_per_hour * horizon_hours * utilization

    if rental_price_per_hour == 0:
        break_even_utilization = float("inf")
    else:
        break_even_utilization = owner_rate / rental_price_per_hour

    if own_total_cost < rent_total_cost:
        cheaper_option, savings = "own", rent_total_cost - own_total_cost
    else:
        cheaper_option, savings = "rent", own_total_cost - rent_total_cost

    return RentVsBuy(
        rental_price_per_hour=rental_price_per_hour,
        owner_cost_per_provisioned_hour=owner_rate,
        break_even_utilization=break_even_utilization,
        horizon_months=horizon_months,
        horizon_hours=horizon_hours,
        own_total_cost=own_total_cost,
        rent_total_cost=rent_total_cost,
        cheaper_option=cheaper_option,
        savings_of_cheaper=savings,
    )


def rent_vs_buy_curve(
    scenario: Scenario,
    rental_price_per_hour: float,
    utilizations: tuple[float, ...],
    horizon_months: float = 36.0,
) -> list[dict[str, float]]:
    """Chart rows: own cost (flat) vs rent cost (linear in utilization)."""
    hourly = cost_per_hour(scenario)
    horizon_hours = horizon_months / 12 * HOURS_PER_YEAR
    own_total_cost = hourly.total_per_provisioned_hour * horizon_hours

    rows: list[dict[str, float]] = []
    for utilization in utilizations:
        rent_total_cost = rental_price_per_hour * horizon_hours * utilization
        rows.append(
            {
                "utilization": utilization,
                "own_total_cost": own_total_cost,
                "rent_total_cost": rent_total_cost,
                "savings_from_owning": rent_total_cost - own_total_cost,
            }
        )
    return rows
