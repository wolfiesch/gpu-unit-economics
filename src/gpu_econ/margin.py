"""Gross-margin calculations for usage-based GPU pricing."""

from __future__ import annotations

from dataclasses import dataclass

from gpu_econ.cost_per_hour import cost_per_hour
from gpu_econ.inputs import HOURS_PER_YEAR, Scenario


@dataclass(frozen=True)
class MarginResult:
    """Gross margin for one GPU sold by the billable hour."""

    price_per_billable_hour: float
    cost_per_billable_hour: float
    gross_profit_per_billable_hour: float
    gross_margin_pct: float
    annual_gross_profit_per_gpu: float


def gross_margin(
    scenario: Scenario,
    price_per_billable_hour: float | None = None,
) -> MarginResult:
    """Compare billable-hour price to fully loaded billable-hour cost."""

    price = (
        scenario.workload.on_demand_price_per_gpu_hour
        if price_per_billable_hour is None
        else price_per_billable_hour
    )
    cost_per_billable_hour = cost_per_hour(scenario).total_per_billable_hour
    gross_profit_per_billable_hour = price - cost_per_billable_hour
    gross_margin_pct = gross_profit_per_billable_hour / price if price > 0 else 0.0
    annual_gross_profit_per_gpu = (
        gross_profit_per_billable_hour * HOURS_PER_YEAR * scenario.workload.utilization
    )

    return MarginResult(
        price_per_billable_hour=price,
        cost_per_billable_hour=cost_per_billable_hour,
        gross_profit_per_billable_hour=gross_profit_per_billable_hour,
        gross_margin_pct=gross_margin_pct,
        annual_gross_profit_per_gpu=annual_gross_profit_per_gpu,
    )


def margin_at_prices(scenario: Scenario, prices: list[float]) -> list[MarginResult]:
    """Return gross-margin results for each candidate billable-hour price."""

    return [gross_margin(scenario, price) for price in prices]
