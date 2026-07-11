"""Demand-based fleet sizing and rent-versus-own decision support."""

from __future__ import annotations

import math
from dataclasses import dataclass

from gpu_econ.cost_per_hour import cost_per_hour
from gpu_econ.inputs import HOURS_PER_YEAR, Scenario

HOURS_PER_MONTH = HOURS_PER_YEAR / 12
SECONDS_PER_HOUR = 3600


@dataclass(frozen=True)
class FleetPlan:
    """Capacity and cost plan for satisfying a fixed monthly token demand."""

    monthly_token_demand: float
    capacity_headroom: float
    fleet_size: int
    monthly_token_capacity: float
    capacity_coverage: float
    active_gpu_hours_per_month: float
    upfront_capex: float
    monthly_ownership_cost: float
    monthly_rental_cost: float
    horizon_months: float
    own_total_cost: float
    rent_total_cost: float
    cheaper_option: str
    savings_of_cheaper: float


def size_fleet(
    scenario: Scenario,
    monthly_token_demand: float,
    rental_price_per_hour: float,
    capacity_headroom: float = 0.15,
    horizon_months: float = 36.0,
) -> FleetPlan:
    """Size an owned fleet and compare its economic cost with elastic renting.

    Headroom is the fraction of installed capacity deliberately kept available
    for traffic spikes or failed hardware. Renting is charged only for the exact
    active GPU-hours needed because cloud capacity can scale down between jobs.
    Ownership uses the fully-loaded provisioned cost for every wall-clock hour.
    """
    if monthly_token_demand <= 0:
        raise ValueError("monthly_token_demand must be positive")
    if rental_price_per_hour < 0:
        raise ValueError("rental_price_per_hour must be non-negative")
    if not 0 <= capacity_headroom < 1:
        raise ValueError("capacity_headroom must be in [0, 1)")
    if horizon_months <= 0:
        raise ValueError("horizon_months must be positive")

    gpu = scenario.gpu
    utilization = scenario.workload.utilization
    raw_tokens_per_gpu_month = (
        gpu.tokens_per_sec * SECONDS_PER_HOUR * HOURS_PER_MONTH * utilization
    )
    usable_tokens_per_gpu_month = raw_tokens_per_gpu_month * (1 - capacity_headroom)
    fleet_size = math.ceil(monthly_token_demand / usable_tokens_per_gpu_month)
    monthly_token_capacity = fleet_size * usable_tokens_per_gpu_month
    active_gpu_hours_per_month = monthly_token_demand / (
        gpu.tokens_per_sec * SECONDS_PER_HOUR
    )

    hourly = cost_per_hour(scenario)
    monthly_ownership_cost = (
        fleet_size * HOURS_PER_MONTH * hourly.total_per_provisioned_hour
    )
    monthly_rental_cost = active_gpu_hours_per_month * rental_price_per_hour
    own_total_cost = monthly_ownership_cost * horizon_months
    rent_total_cost = monthly_rental_cost * horizon_months

    if own_total_cost < rent_total_cost:
        cheaper_option = "own"
        savings = rent_total_cost - own_total_cost
    else:
        cheaper_option = "rent"
        savings = own_total_cost - rent_total_cost

    return FleetPlan(
        monthly_token_demand=monthly_token_demand,
        capacity_headroom=capacity_headroom,
        fleet_size=fleet_size,
        monthly_token_capacity=monthly_token_capacity,
        capacity_coverage=monthly_token_capacity / monthly_token_demand,
        active_gpu_hours_per_month=active_gpu_hours_per_month,
        upfront_capex=fleet_size * gpu.capex_usd,
        monthly_ownership_cost=monthly_ownership_cost,
        monthly_rental_cost=monthly_rental_cost,
        horizon_months=horizon_months,
        own_total_cost=own_total_cost,
        rent_total_cost=rent_total_cost,
        cheaper_option=cheaper_option,
        savings_of_cheaper=savings,
    )
