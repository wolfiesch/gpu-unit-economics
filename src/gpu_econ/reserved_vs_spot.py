"""Reserved versus on-demand GPU-hour buyer economics."""

from __future__ import annotations

from dataclasses import dataclass

from gpu_econ.inputs import HOURS_PER_YEAR, Scenario


@dataclass(frozen=True)
class BreakEven:
    """Buyer break-even and actual term cost comparison."""

    break_even_utilization: float
    term_hours: float
    reserved_total_cost: float
    on_demand_total_cost: float
    cheaper_option: str
    savings_of_cheaper: float


def break_even(scenario: Scenario) -> BreakEven:
    """Solve u* = reserved_rate / on_demand_rate and compare actual term costs."""
    workload = scenario.workload
    term_hours = _term_hours(scenario)
    reserved_total_cost = workload.reserved_price_per_gpu_hour * term_hours
    on_demand_total_cost = (
        workload.on_demand_price_per_gpu_hour * term_hours * workload.utilization
    )
    cheaper_option, savings_of_cheaper = _cheaper_option_and_savings(
        reserved_total_cost, on_demand_total_cost
    )

    return BreakEven(
        break_even_utilization=_break_even_utilization(scenario),
        term_hours=term_hours,
        reserved_total_cost=reserved_total_cost,
        on_demand_total_cost=on_demand_total_cost,
        cheaper_option=cheaper_option,
        savings_of_cheaper=savings_of_cheaper,
    )


def break_even_curve(
    scenario: Scenario, utilizations: tuple[float, ...]
) -> list[dict[str, float]]:
    """Return chart rows for reserved cost, on-demand cost, and reservation savings."""
    workload = scenario.workload
    term_hours = _term_hours(scenario)
    reserved_total_cost = workload.reserved_price_per_gpu_hour * term_hours

    rows: list[dict[str, float]] = []
    for utilization in utilizations:
        on_demand_total_cost = (
            workload.on_demand_price_per_gpu_hour * term_hours * utilization
        )
        rows.append(
            {
                "utilization": utilization,
                "reserved_total_cost": reserved_total_cost,
                "on_demand_total_cost": on_demand_total_cost,
                "savings_from_reserving": on_demand_total_cost - reserved_total_cost,
            }
        )
    return rows


def _term_hours(scenario: Scenario) -> float:
    return scenario.workload.reserved_term_months / 12 * HOURS_PER_YEAR


def _break_even_utilization(scenario: Scenario) -> float:
    workload = scenario.workload
    if workload.on_demand_price_per_gpu_hour == 0:
        if workload.reserved_price_per_gpu_hour == 0:
            return 0.0
        return float("inf")
    return workload.reserved_price_per_gpu_hour / workload.on_demand_price_per_gpu_hour


def _cheaper_option_and_savings(
    reserved_total_cost: float, on_demand_total_cost: float
) -> tuple[str, float]:
    if reserved_total_cost < on_demand_total_cost:
        return "reserved", on_demand_total_cost - reserved_total_cost
    return "on_demand", reserved_total_cost - on_demand_total_cost
