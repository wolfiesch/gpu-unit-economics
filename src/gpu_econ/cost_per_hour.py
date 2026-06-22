"""Fully loaded GPU-hour cost calculations."""

from __future__ import annotations

from dataclasses import dataclass

from gpu_econ.inputs import HOURS_PER_YEAR, Scenario


@dataclass(frozen=True)
class HourlyCost:
    """Cost components for one GPU-hour under a scenario."""

    depreciation_per_hour: float
    power_per_hour: float
    opex_per_hour: float
    total_per_provisioned_hour: float
    total_per_billable_hour: float


def cost_per_hour(scenario: Scenario) -> HourlyCost:
    """Compute provisioned cost as depreciation + power + opex.

    Billable-hour cost is provisioned-hour cost divided by utilization.
    """
    gpu = scenario.gpu
    datacenter = scenario.datacenter
    workload = scenario.workload

    annual_depreciation = (
        gpu.capex_usd * (1 - gpu.residual_value_frac) / gpu.useful_life_years
    )
    depreciation_per_hour = annual_depreciation / HOURS_PER_YEAR
    power_per_hour = gpu.power_kw * datacenter.pue * datacenter.power_cost_per_kwh
    opex_per_hour = (
        gpu.capex_usd * datacenter.opex_frac_of_capex_per_year / HOURS_PER_YEAR
    )
    total_per_provisioned_hour = depreciation_per_hour + power_per_hour + opex_per_hour
    total_per_billable_hour = total_per_provisioned_hour / workload.utilization

    return HourlyCost(
        depreciation_per_hour=depreciation_per_hour,
        power_per_hour=power_per_hour,
        opex_per_hour=opex_per_hour,
        total_per_provisioned_hour=total_per_provisioned_hour,
        total_per_billable_hour=total_per_billable_hour,
    )


def cost_breakdown(scenario: Scenario) -> dict[str, float]:
    """Return the same cost_per_hour components keyed by field name."""
    hourly_cost = cost_per_hour(scenario)
    return {
        "depreciation_per_hour": hourly_cost.depreciation_per_hour,
        "power_per_hour": hourly_cost.power_per_hour,
        "opex_per_hour": hourly_cost.opex_per_hour,
        "total_per_provisioned_hour": hourly_cost.total_per_provisioned_hour,
        "total_per_billable_hour": hourly_cost.total_per_billable_hour,
    }
