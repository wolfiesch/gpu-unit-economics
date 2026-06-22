"""Command-line scenario runner for the GPU unit-economics model.

Runs all five calculations against the default GPU set (H100/H200/B200) under the
default data-center and workload assumptions, and prints readable tables. No flags
required: `gpu-econ` (or `python -m gpu_econ.cli`) prints the full report.

This is a thin presentation layer — all numbers come from the calculation modules.
"""

from __future__ import annotations

from .cost_per_hour import cost_per_hour
from .cost_per_token import compare_gpus
from .depreciation import ebitda_swing, sensitivity
from .inputs import DEFAULT_GPUS, Scenario
from .margin import gross_margin
from .reserved_vs_spot import break_even


def _usd(x: float) -> str:
    return f"${x:,.2f}"


def _section(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def cost_per_hour_table() -> None:
    _section("1. Fully-loaded cost per GPU-hour (default assumptions)")
    print(
        f"{'GPU':<6} {'depr/hr':>9} {'power/hr':>9} {'opex/hr':>9} "
        f"{'$/prov-hr':>10} {'$/billable-hr':>14}"
    )
    for gpu in DEFAULT_GPUS:
        c = cost_per_hour(Scenario(gpu=gpu))
        print(
            f"{gpu.name:<6} {_usd(c.depreciation_per_hour):>9} {_usd(c.power_per_hour):>9} "
            f"{_usd(c.opex_per_hour):>9} {_usd(c.total_per_provisioned_hour):>10} "
            f"{_usd(c.total_per_billable_hour):>14}"
        )


def cost_per_token_table() -> None:
    _section("2. Cost per 1M tokens (inference, sorted cheapest first)")
    print(f"{'GPU':<6} {'tok/hr (eff)':>14} {'$/prov-hr':>10} {'$/1M tokens':>12}")
    for t in compare_gpus(DEFAULT_GPUS):
        print(
            f"{t.gpu_name:<6} {t.effective_tokens_per_hour:>14,.0f} "
            f"{_usd(t.cost_per_provisioned_hour):>10} {_usd(t.cost_per_million_tokens):>12}"
        )


def margin_table() -> None:
    _section("3. Gross margin at on-demand list price (per billable hour)")
    print(
        f"{'GPU':<6} {'price/hr':>9} {'cost/hr':>9} {'profit/hr':>10} "
        f"{'margin':>8} {'annual GP/GPU':>14}"
    )
    for gpu in DEFAULT_GPUS:
        m = gross_margin(Scenario(gpu=gpu))
        print(
            f"{gpu.name:<6} {_usd(m.price_per_billable_hour):>9} "
            f"{_usd(m.cost_per_billable_hour):>9} {_usd(m.gross_profit_per_billable_hour):>10} "
            f"{m.gross_margin_pct:>7.1%} {_usd(m.annual_gross_profit_per_gpu):>14}"
        )


def depreciation_table() -> None:
    _section("4. Depreciation useful-life sensitivity (H100, the 3-vs-6-year debate)")
    print(f"{'life (yr)':>9} {'depr/hr':>9} {'$/prov-hr':>10} {'annual depr/GPU':>16}")
    h100 = DEFAULT_GPUS[0]
    for row in sensitivity(Scenario(gpu=h100)):
        print(
            f"{row.useful_life_years:>9.0f} {_usd(row.depreciation_per_hour):>9} "
            f"{_usd(row.total_cost_per_provisioned_hour):>10} "
            f"{_usd(row.annual_depreciation_per_gpu):>16}"
        )
    swing = ebitda_swing(Scenario(gpu=h100), base_life=6.0, alt_life=3.0, fleet_size=1000)
    # delta = 6yr depreciation - 3yr depreciation (negative: 6yr expenses less => more EBITDA)
    ebitda_boost = -swing["ebitda_delta_usd"]
    print(
        f"\n  Fleet of 1,000 H100s: 6-year life reports {_usd(abs(ebitda_boost))} "
        f"{'more' if ebitda_boost > 0 else 'less'} annual EBITDA than 3-year life."
    )


def reserved_vs_spot_table() -> None:
    _section("5. Reserved vs on-demand break-even (buyer view, 12-month term)")
    h100 = DEFAULT_GPUS[0]
    b = break_even(Scenario(gpu=h100))
    print(f"Break-even utilization: {b.break_even_utilization:.1%}")
    print(f"  Reserved total (term):  {_usd(b.reserved_total_cost)}")
    print(f"  On-demand total (term): {_usd(b.on_demand_total_cost)}")
    print(
        f"  Cheaper at default 70% utilization: {b.cheaper_option} "
        f"(saves {_usd(b.savings_of_cheaper)})"
    )


def main() -> None:
    print("GPU Unit Economics — default scenario report")
    print("=" * 44)
    print("Assumptions are illustrative (see README). All figures USD.")
    cost_per_hour_table()
    cost_per_token_table()
    margin_table()
    depreciation_table()
    reserved_vs_spot_table()
    print()


if __name__ == "__main__":
    main()
