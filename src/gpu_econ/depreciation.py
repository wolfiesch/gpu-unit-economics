"""Useful-life sensitivity for GPU depreciation assumptions."""

from __future__ import annotations

from dataclasses import dataclass, replace

from gpu_econ.cost_per_hour import cost_per_hour
from gpu_econ.inputs import HOURS_PER_YEAR, Scenario


@dataclass(frozen=True)
class LifeSensitivity:
    """Cost result for one useful-life assumption."""

    useful_life_years: float
    depreciation_per_hour: float
    total_cost_per_provisioned_hour: float
    annual_depreciation_per_gpu: float


def sensitivity(
    scenario: Scenario,
    lives: tuple[float, ...] = (3.0, 4.0, 5.0, 6.0),
) -> list[LifeSensitivity]:
    """Reprice straight-line depreciation and fully loaded cost for each useful life."""
    rows: list[LifeSensitivity] = []
    for useful_life_years in lives:
        scenario_for_life = _scenario_with_useful_life(scenario, useful_life_years)
        annual_depreciation = _annual_depreciation_per_gpu(scenario_for_life)
        loaded_cost = cost_per_hour(scenario_for_life)
        rows.append(
            LifeSensitivity(
                useful_life_years=useful_life_years,
                depreciation_per_hour=annual_depreciation / HOURS_PER_YEAR,
                total_cost_per_provisioned_hour=loaded_cost.total_per_provisioned_hour,
                annual_depreciation_per_gpu=annual_depreciation,
            )
        )
    return rows


def ebitda_swing(
    scenario: Scenario,
    base_life: float,
    alt_life: float,
    fleet_size: int = 1,
) -> dict[str, float]:
    """Return fleet depreciation and EBITDA delta: (base dep - alt dep) * fleet size."""
    base_annual_depreciation = _annual_depreciation_per_gpu(
        _scenario_with_useful_life(scenario, base_life)
    )
    alt_annual_depreciation = _annual_depreciation_per_gpu(
        _scenario_with_useful_life(scenario, alt_life)
    )

    base_fleet_depreciation = base_annual_depreciation * fleet_size
    alt_fleet_depreciation = alt_annual_depreciation * fleet_size

    return {
        "base_annual_depreciation_usd": base_fleet_depreciation,
        "alt_annual_depreciation_usd": alt_fleet_depreciation,
        "ebitda_delta_usd": base_fleet_depreciation - alt_fleet_depreciation,
    }


def _scenario_with_useful_life(scenario: Scenario, useful_life_years: float) -> Scenario:
    gpu = replace(scenario.gpu, useful_life_years=useful_life_years)
    return replace(scenario, gpu=gpu)


def _annual_depreciation_per_gpu(scenario: Scenario) -> float:
    gpu = scenario.gpu
    return gpu.capex_usd * (1 - gpu.residual_value_frac) / gpu.useful_life_years
