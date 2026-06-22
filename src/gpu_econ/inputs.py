"""Shared input contract for the GPU unit-economics model.

Every calculation module imports these dataclasses. They are the single source of
truth for assumptions. All money is USD; all power is kW; all time is hours unless
a field name says otherwise. Nothing here computes — these are pure inputs.

Default values are illustrative, sourced from public GPU specs and typical
data-center figures (see README for citations). They are assumptions, not quotes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

HOURS_PER_YEAR = 8760  # 365 * 24


@dataclass(frozen=True)
class GPUSpec:
    """Physical + capital characteristics of a single GPU (and its server share).

    `capex_usd` should be the all-in acquisition cost attributable to one GPU,
    i.e. the GPU plus its amortized share of the host server, NVLink/networking,
    and rack. `power_kw` is the GPU's own board power; system overhead (cooling,
    networking, host CPU) is captured separately via DataCenterAssumptions.pue and
    overhead factors so it is not double-counted.
    """

    name: str
    capex_usd: float
    power_kw: float
    # Sustained inference throughput in tokens/second for the reference workload.
    # This is a per-GPU figure at full utilization for a representative model size.
    tokens_per_sec: float
    useful_life_years: float = 4.0
    residual_value_frac: float = 0.10  # fraction of capex recovered at end of life

    def __post_init__(self) -> None:
        if self.capex_usd <= 0:
            raise ValueError("capex_usd must be positive")
        if self.power_kw <= 0:
            raise ValueError("power_kw must be positive")
        if self.tokens_per_sec <= 0:
            raise ValueError("tokens_per_sec must be positive")
        if self.useful_life_years <= 0:
            raise ValueError("useful_life_years must be positive")
        if not 0.0 <= self.residual_value_frac < 1.0:
            raise ValueError("residual_value_frac must be in [0, 1)")


@dataclass(frozen=True)
class DataCenterAssumptions:
    """Operating environment shared across GPUs."""

    power_cost_per_kwh: float = 0.08  # blended industrial $/kWh
    pue: float = 1.3  # power usage effectiveness (total / IT power)
    # Non-power opex as a fraction of GPU capex per year (staff, maintenance,
    # bandwidth, real estate not captured in power). Keeps the model honest about
    # costs beyond depreciation + electricity.
    opex_frac_of_capex_per_year: float = 0.05

    def __post_init__(self) -> None:
        if self.power_cost_per_kwh < 0:
            raise ValueError("power_cost_per_kwh must be non-negative")
        if self.pue < 1.0:
            raise ValueError("pue must be >= 1.0")
        if self.opex_frac_of_capex_per_year < 0:
            raise ValueError("opex_frac_of_capex_per_year must be non-negative")


@dataclass(frozen=True)
class WorkloadAssumptions:
    """How the fleet is actually used and sold."""

    utilization: float = 0.70  # fraction of wall-clock the GPU is doing billable work
    # Price the operator charges customers per GPU-hour (on-demand list).
    on_demand_price_per_gpu_hour: float = 2.50
    # Discounted price for a committed/reserved GPU-hour.
    reserved_price_per_gpu_hour: float = 1.60
    # Contract length for a reservation, in months (used by reserved-vs-spot).
    reserved_term_months: int = 12

    def __post_init__(self) -> None:
        if not 0.0 < self.utilization <= 1.0:
            raise ValueError("utilization must be in (0, 1]")
        if self.on_demand_price_per_gpu_hour < 0:
            raise ValueError("on_demand_price_per_gpu_hour must be non-negative")
        if self.reserved_price_per_gpu_hour < 0:
            raise ValueError("reserved_price_per_gpu_hour must be non-negative")
        if self.reserved_term_months <= 0:
            raise ValueError("reserved_term_months must be positive")


@dataclass(frozen=True)
class Scenario:
    """A full set of assumptions: one GPU + environment + workload."""

    gpu: GPUSpec
    datacenter: DataCenterAssumptions = field(default_factory=DataCenterAssumptions)
    workload: WorkloadAssumptions = field(default_factory=WorkloadAssumptions)


# --- Illustrative default GPU specs (assumptions; see README for sources) ---------

H100 = GPUSpec(name="H100", capex_usd=30_000, power_kw=0.70, tokens_per_sec=2_500)
H200 = GPUSpec(name="H200", capex_usd=35_000, power_kw=0.70, tokens_per_sec=3_400)
B200 = GPUSpec(name="B200", capex_usd=45_000, power_kw=1.00, tokens_per_sec=6_000)

DEFAULT_GPUS = (H100, H200, B200)
