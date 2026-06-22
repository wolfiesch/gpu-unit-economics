"""Cost per million output tokens for inference workloads."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from gpu_econ.cost_per_hour import cost_per_hour
from gpu_econ.inputs import (
    DEFAULT_GPUS,
    DataCenterAssumptions,
    GPUSpec,
    Scenario,
    WorkloadAssumptions,
)

TOKENS_PER_MILLION = 1_000_000
SECONDS_PER_HOUR = 3_600


@dataclass(frozen=True)
class TokenCost:
    """Cost and effective throughput for one GPU under one inference scenario."""

    gpu_name: str
    effective_tokens_per_hour: float
    cost_per_provisioned_hour: float
    cost_per_million_tokens: float


def cost_per_million_tokens(scenario: Scenario) -> TokenCost:
    """Return loaded provisioned-hour cost divided by utilized tokens per hour."""
    hourly_cost = cost_per_hour(scenario).total_per_provisioned_hour
    effective_tokens_per_hour = (
        scenario.gpu.tokens_per_sec * SECONDS_PER_HOUR * scenario.workload.utilization
    )
    cost_per_million = hourly_cost / (effective_tokens_per_hour / TOKENS_PER_MILLION)

    return TokenCost(
        gpu_name=scenario.gpu.name,
        effective_tokens_per_hour=effective_tokens_per_hour,
        cost_per_provisioned_hour=hourly_cost,
        cost_per_million_tokens=cost_per_million,
    )


def compare_gpus(
    gpus: Iterable[GPUSpec] | None = None,
    datacenter: DataCenterAssumptions | None = None,
    workload: WorkloadAssumptions | None = None,
) -> list[TokenCost]:
    """Return GPUs sorted by loaded cost per 1M tokens for matching assumptions."""
    gpu_specs = DEFAULT_GPUS if gpus is None else gpus
    datacenter_assumptions = datacenter if datacenter is not None else DataCenterAssumptions()
    workload_assumptions = workload if workload is not None else WorkloadAssumptions()

    results = [
        cost_per_million_tokens(
            Scenario(
                gpu=gpu,
                datacenter=datacenter_assumptions,
                workload=workload_assumptions,
            )
        )
        for gpu in gpu_specs
    ]

    return sorted(results, key=lambda result: result.cost_per_million_tokens)
