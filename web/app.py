"""FastAPI wrapper around the gpu_econ unit-economics model.

Exposes a single /compute endpoint that accepts datacenter + workload assumptions
and a list of GPU specs, and returns all five model outputs computed for each GPU.
Serves a single-page interactive dashboard at /.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from gpu_econ import benchmarks
from gpu_econ.cost_per_hour import cost_per_hour
from gpu_econ.cost_per_token import cost_per_million_tokens
from gpu_econ.depreciation import book_value_curve, ebitda_swing, sensitivity
from gpu_econ.fleet_sizing import size_fleet
from gpu_econ.inputs import (
    DEFAULT_GPUS,
    DataCenterAssumptions,
    GPUSpec,
    Scenario,
    WorkloadAssumptions,
)
from gpu_econ.margin import gross_margin
from gpu_econ.rent_vs_buy import rent_vs_buy, rent_vs_buy_curve
from gpu_econ.reserved_vs_spot import break_even, break_even_curve

from . import geo, historical, power, token_prices
from .providers import CANONICAL_GPUS
from .store import PriceStore

FORCE_TOKEN = os.environ.get("FORCE_TOKEN")

LIVES = (3.0, 4.0, 5.0, 6.0)
UTIL_CURVE = tuple(round(0.05 * i, 2) for i in range(1, 21))  # 0.05 .. 1.00

app = FastAPI(
    title="GPU Unit Economics",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

WEB_DIR = Path(__file__).resolve().parent
price_store = PriceStore()


# --- Request / response schemas -------------------------------------------------

class GpuInput(BaseModel):
    name: str
    capex_usd: float = Field(gt=0)
    power_kw: float = Field(gt=0)
    tokens_per_sec: float = Field(gt=0)
    useful_life_years: float = Field(default=4.0, gt=0)
    residual_value_frac: float = Field(default=0.10, ge=0, lt=1)


class DataCenterInput(BaseModel):
    power_cost_per_kwh: float = Field(default=0.08, ge=0)
    pue: float = Field(default=1.3, ge=1.0)
    opex_frac_of_capex_per_year: float = Field(default=0.05, ge=0)


class WorkloadInput(BaseModel):
    utilization: float = Field(default=0.70, gt=0, le=1)
    on_demand_price_per_gpu_hour: float = Field(default=2.50, ge=0)
    reserved_price_per_gpu_hour: float = Field(default=1.60, ge=0)
    reserved_term_months: int = Field(default=12, gt=0)


class ComputeRequest(BaseModel):
    gpus: list[GpuInput] = Field(min_length=1)
    datacenter: DataCenterInput = Field(default_factory=DataCenterInput)
    workload: WorkloadInput = Field(default_factory=WorkloadInput)
    fleet_size: int = Field(default=1000, gt=0)
    # Per-GPU live rental $/hr overlay (canonical name -> price). GPUs missing
    # from the map fall back to the global on-demand price assumption.
    rental_prices: dict[str, float] = Field(default_factory=dict)
    rent_horizon_months: float = Field(default=36.0, gt=0)
    monthly_token_demand: float = Field(default=20_000_000_000, gt=0)
    capacity_headroom: float = Field(default=0.15, ge=0, lt=1)


# --- Helpers ---------------------------------------------------------------------

def _build_scenario(gpu_in: GpuInput, dc_in: DataCenterInput, wl_in: WorkloadInput) -> Scenario:
    gpu = GPUSpec(
        name=gpu_in.name,
        capex_usd=gpu_in.capex_usd,
        power_kw=gpu_in.power_kw,
        tokens_per_sec=gpu_in.tokens_per_sec,
        useful_life_years=gpu_in.useful_life_years,
        residual_value_frac=gpu_in.residual_value_frac,
    )
    dc = DataCenterAssumptions(
        power_cost_per_kwh=dc_in.power_cost_per_kwh,
        pue=dc_in.pue,
        opex_frac_of_capex_per_year=dc_in.opex_frac_of_capex_per_year,
    )
    wl = WorkloadAssumptions(
        utilization=wl_in.utilization,
        on_demand_price_per_gpu_hour=wl_in.on_demand_price_per_gpu_hour,
        reserved_price_per_gpu_hour=wl_in.reserved_price_per_gpu_hour,
        reserved_term_months=wl_in.reserved_term_months,
    )
    return Scenario(gpu=gpu, datacenter=dc, workload=wl)


def _per_gpu(
    scenario: Scenario,
    fleet_size: int,
    rental_price: float,
    rent_horizon_months: float,
    monthly_token_demand: float,
    capacity_headroom: float,
) -> dict[str, Any]:
    hc = cost_per_hour(scenario)
    tc = cost_per_million_tokens(scenario)
    mg = gross_margin(scenario)
    sens = sensitivity(scenario, LIVES)
    swing = ebitda_swing(scenario, base_life=3.0, alt_life=6.0, fleet_size=fleet_size)
    be = break_even(scenario)
    curve = break_even_curve(scenario, UTIL_CURVE)
    rvb = rent_vs_buy(scenario, rental_price, rent_horizon_months)
    rvb_curve = rent_vs_buy_curve(scenario, rental_price, UTIL_CURVE, rent_horizon_months)
    fleet = size_fleet(
        scenario,
        monthly_token_demand,
        rental_price,
        capacity_headroom,
        rent_horizon_months,
    )

    return {
        "name": scenario.gpu.name,
        "cost_per_hour": {
            "depreciation": hc.depreciation_per_hour,
            "power": hc.power_per_hour,
            "opex": hc.opex_per_hour,
            "provisioned": hc.total_per_provisioned_hour,
            "billable": hc.total_per_billable_hour,
        },
        "cost_per_million_tokens": tc.cost_per_million_tokens,
        "effective_tokens_per_hour": tc.effective_tokens_per_hour,
        "margin": {
            "price_per_billable_hour": mg.price_per_billable_hour,
            "cost_per_billable_hour": mg.cost_per_billable_hour,
            "gross_profit_per_hour": mg.gross_profit_per_billable_hour,
            "margin_pct": mg.gross_margin_pct,
            "annual_gp_per_gpu": mg.annual_gross_profit_per_gpu,
        },
        "depreciation_sensitivity": [
            {
                "life": s.useful_life_years,
                "depreciation_per_hour": s.depreciation_per_hour,
                "provisioned_cost": s.total_cost_per_provisioned_hour,
                "annual_depreciation": s.annual_depreciation_per_gpu,
            }
            for s in sens
        ],
        "ebitda_swing_3v6": swing,
        "break_even": {
            "utilization": be.break_even_utilization,
            "term_hours": be.term_hours,
            "reserved_cost": be.reserved_total_cost,
            "on_demand_cost": be.on_demand_total_cost,
            "cheaper": be.cheaper_option,
            "savings": be.savings_of_cheaper,
        },
        "break_even_curve": curve,
        "rent_vs_buy": {
            "rental_price_per_hour": rvb.rental_price_per_hour,
            "owner_cost_per_provisioned_hour": rvb.owner_cost_per_provisioned_hour,
            "break_even_utilization": (
                None
                if rvb.break_even_utilization == float("inf")
                else rvb.break_even_utilization
            ),
            "horizon_months": rvb.horizon_months,
            "own_total_cost": rvb.own_total_cost,
            "rent_total_cost": rvb.rent_total_cost,
            "cheaper": rvb.cheaper_option,
            "savings": rvb.savings_of_cheaper,
        },
        "rent_vs_buy_curve": rvb_curve,
        "fleet_plan": {
            "monthly_token_demand": fleet.monthly_token_demand,
            "capacity_headroom": fleet.capacity_headroom,
            "fleet_size": fleet.fleet_size,
            "monthly_token_capacity": fleet.monthly_token_capacity,
            "capacity_coverage": fleet.capacity_coverage,
            "active_gpu_hours_per_month": fleet.active_gpu_hours_per_month,
            "upfront_capex": fleet.upfront_capex,
            "monthly_ownership_cost": fleet.monthly_ownership_cost,
            "monthly_rental_cost": fleet.monthly_rental_cost,
            "horizon_months": fleet.horizon_months,
            "own_total_cost": fleet.own_total_cost,
            "rent_total_cost": fleet.rent_total_cost,
            "cheaper": fleet.cheaper_option,
            "savings": fleet.savings_of_cheaper,
        },
        "book_value_curves": {
            str(life): book_value_curve(scenario, life, 72) for life in LIVES
        },
    }


# --- Routes ---------------------------------------------------------------------

@app.post("/compute")
def compute(req: ComputeRequest) -> dict[str, Any]:
    try:
        results = [
            _per_gpu(
                _build_scenario(g, req.datacenter, req.workload),
                req.fleet_size,
                req.rental_prices.get(g.name, req.workload.on_demand_price_per_gpu_hour),
                req.rent_horizon_months,
                req.monthly_token_demand,
                req.capacity_headroom,
            )
            for g in req.gpus
        ]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    token_ranked = sorted(
        [
            {
                "name": r["name"],
                "cost_per_million_tokens": r["cost_per_million_tokens"],
            }
            for r in results
        ],
        key=lambda x: x["cost_per_million_tokens"],
    )

    alternatives = sorted(
        (
            {
                "gpu": r["name"],
                "option": option,
                "total_cost": r["fleet_plan"][f"{option}_total_cost"],
                "monthly_cost": r["fleet_plan"][
                    "monthly_ownership_cost" if option == "own" else "monthly_rental_cost"
                ],
                "fleet_size": r["fleet_plan"]["fleet_size"],
                "upfront_capex": r["fleet_plan"]["upfront_capex"] if option == "own" else 0,
            }
            for r in results
            for option in ("own", "rent")
        ),
        key=lambda x: x["total_cost"],
    )
    best = alternatives[0]
    runner_up = alternatives[1] if len(alternatives) > 1 else best
    decision_summary = {
        **best,
        "savings_vs_next_best": runner_up["total_cost"] - best["total_cost"],
        "next_best_gpu": runner_up["gpu"],
        "next_best_option": runner_up["option"],
        "horizon_months": req.rent_horizon_months,
        "monthly_token_demand": req.monthly_token_demand,
        "capacity_headroom": req.capacity_headroom,
    }

    return {
        "results": results,
        "token_ranking": token_ranked,
        "decision_summary": decision_summary,
    }


@app.get("/defaults")
def defaults() -> dict[str, Any]:
    """Return the default GPU specs + assumptions so the UI can initialize."""
    return {
        "gpus": [
            {"name": g.name, "capex_usd": g.capex_usd, "power_kw": g.power_kw,
             "tokens_per_sec": g.tokens_per_sec, "useful_life_years": g.useful_life_years,
             "residual_value_frac": g.residual_value_frac}
            for g in DEFAULT_GPUS
        ],
        "datacenter": {
            "power_cost_per_kwh": 0.08,
            "pue": 1.3,
            "opex_frac_of_capex_per_year": 0.05,
        },
        "workload": {
            "utilization": 0.70,
            "on_demand_price_per_gpu_hour": 2.50,
            "reserved_price_per_gpu_hour": 1.60,
            "reserved_term_months": 12,
        },
        "fleet_size": 1000,
        "monthly_token_demand": 20_000_000_000,
        "capacity_headroom": 0.15,
    }


# Live-data responses must not be edge-cached: the app already has its own TTL
# cache, and a CDN-stale copy silently defeats the 15-minute poller.
@app.middleware("http")
async def cache_policy(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    elif path == "/" or path.startswith("/static/"):
        # Frequently redeployed alongside API changes; keep edge cache short so
        # the UI and its endpoints never skew more than 5 minutes apart.
        response.headers["Cache-Control"] = "public, max-age=300, must-revalidate"
    return response


# --- Live market data ------------------------------------------------------------

@app.get("/api/prices")
def prices(force: bool = False, x_force_token: str | None = Header(default=None)) -> dict[str, Any]:
    """Latest live GPU rental prices, served through a TTL cache.

    Fetches from providers only when the cached batch is older than the TTL;
    on upstream failure returns the last known snapshot with `stale: true`.
    `force=true` bypasses the TTL and requires the X-Force-Token header to match
    FORCE_TOKEN when that env var is set (so only the ops poller can hammer refetch).
    """
    if force and FORCE_TOKEN and x_force_token != FORCE_TOKEN:
        raise HTTPException(status_code=403, detail="force refresh requires a valid X-Force-Token")
    return price_store.get_latest(force=force)


@app.get("/api/power")
def power_prices() -> dict[str, Any]:
    """Latest US industrial electricity $/kWh by state (EIA, monthly, day-cached)."""
    if not power.available():
        raise HTTPException(status_code=503, detail="EIA_API_KEY not configured")
    try:
        return power.fetch_state_prices()
    except Exception as exc:  # upstream/parse failure
        raise HTTPException(status_code=502, detail=f"EIA fetch failed: {exc}") from exc


@app.get("/api/benchmarks")
def benchmark_table() -> dict[str, Any]:
    """Published per-GPU throughput estimates by model (static, cited)."""
    return benchmarks.table()


@app.get("/api/prices/historical")
def historical_prices() -> dict[str, Any]:
    """Audited historical GPU/system prices (2016-2025), CPI-normalized."""
    return historical.table()


@app.get("/api/token-prices")
def token_prices_endpoint() -> dict[str, Any]:
    """OpenRouter open-weights token prices (per 1M tokens), day-cached."""
    try:
        return token_prices.fetch_token_prices()
    except Exception as exc:  # upstream/parse failure
        raise HTTPException(status_code=502, detail=f"OpenRouter fetch failed: {exc}") from exc


@app.get("/api/prices/history")
def price_history(gpu: str, hours: float = 24 * 7) -> dict[str, Any]:
    """Price snapshots for one GPU over a trailing window (default 7 days)."""
    if gpu not in CANONICAL_GPUS:
        raise HTTPException(status_code=404, detail=f"unknown gpu {gpu!r}")
    return {"gpu": gpu, "hours": hours, "snapshots": price_store.history(gpu, hours)}


@app.get("/api/prices/regions")
def price_regions() -> dict[str, Any]:
    """Latest regional quotes grouped by GPU, with cross-region spread stats.

    Spread = max/min across all regional quotes in the latest batch. Regions
    are imperfect substitutes (latency, data residency, self-reported Vast
    geolocation), so a persistent spread is not free money — the caveat ships
    in the payload.
    """
    latest = price_store.get_latest()
    by_gpu: dict[str, dict[str, Any]] = {}
    for p in latest["prices"]:
        if not p.get("region"):
            continue  # pre-region rows or providers without geography (RunPod)
        g = by_gpu.setdefault(p["gpu"], {"quotes": []})
        g["quotes"].append(p)
    for g in by_gpu.values():
        quotes = sorted(g["quotes"], key=lambda q: q["price_per_hour"])
        g["quotes"] = quotes
        for q in quotes:
            q["lat"], q["lon"] = geo.coords(q["region"]) or (None, None)
        g["cheapest"] = quotes[0]
        g["priciest"] = quotes[-1]
        g["spread_ratio"] = (
            round(quotes[-1]["price_per_hour"] / quotes[0]["price_per_hour"], 2)
            if quotes[0]["price_per_hour"] > 0 else None
        )
    return {
        "fetched_at": latest["fetched_at"],
        "stale": latest["stale"],
        "caveat": (
            "Regions are not perfect substitutes: latency, data residency, and "
            "self-reported marketplace geolocation all let spreads persist."
        ),
        "gpus": by_gpu,
    }


@app.get("/api/prices/spread")
def price_spread(gpu: str, hours: float = 24 * 30) -> dict[str, Any]:
    """Cross-region min/max per snapshot batch for one GPU (spread over time)."""
    if gpu not in CANONICAL_GPUS:
        raise HTTPException(status_code=404, detail=f"unknown gpu {gpu!r}")
    return {"gpu": gpu, "hours": hours, "batches": price_store.spread_history(gpu, hours)}


@app.get("/")
def index() -> HTMLResponse:
    """Serve the dashboard with a per-deploy asset version for cache busting.

    The version is app.js's mtime, so every deploy naturally invalidates the
    edge-cached HTML's references to /static/* without manual purges.
    """
    static_dir = WEB_DIR / "static"
    mtimes = [
        (static_dir / "app.js").stat().st_mtime,
        (static_dir / "style.css").stat().st_mtime,
    ]
    version = str(int(max(mtimes)))
    html = (static_dir / "index.html").read_text().replace("__V__", version)
    return HTMLResponse(html)

app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
