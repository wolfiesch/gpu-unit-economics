"""FastAPI wrapper around the gpu_econ unit-economics model.

Exposes a single /compute endpoint that accepts datacenter + workload assumptions
and a list of GPU specs, and returns all five model outputs computed for each GPU.
Serves a single-page interactive dashboard at /.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from math import ceil
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from gpu_econ import alerts as alert_engine
from gpu_econ import backtesting, benchmarks, registry, workloads
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

from . import geo, historical, notifications, power, token_prices
from .intelligence_store import IntelligenceStore
from .providers import CANONICAL_GPUS
from .store import PriceStore

FORCE_TOKEN = os.environ.get("FORCE_TOKEN")
ALERT_DELIVERY_TOKEN = os.environ.get("ALERT_DELIVERY_TOKEN")

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
intelligence_store = IntelligenceStore()


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


class WorkloadEvaluationRequest(BaseModel):
    profile: str = "interactive"
    model: str = "llama-3.1-8b"
    average_input_tokens: int = Field(default=1024, gt=0)
    average_output_tokens: int = Field(default=256, gt=0)
    peak_requests_per_second: float = Field(default=2, gt=0)
    latency_target_seconds: float | None = Field(default=2, gt=0)
    capacity_headroom: float = Field(default=0.15, ge=0, lt=1)


class BacktestRequest(BaseModel):
    gpu: str
    decision_at: float = Field(gt=0)
    horizon_hours: float = Field(default=168, gt=0, le=24 * 365)
    scenario: ComputeRequest
    max_quote_age_minutes: float = Field(default=30, gt=0, le=24 * 60)


class AlertRuleRequest(BaseModel):
    gpu: str
    alert_type: str
    threshold: float | None = None
    required_observations: int = Field(default=3, ge=1, le=12)
    cooldown_hours: float = Field(default=24, ge=0, le=24 * 365)
    scenario: ComputeRequest | None = None
    delivery_channel: str = "in_app"
    delivery_target: str = ""


class AlertStatusRequest(BaseModel):
    active: bool


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


def _alert_description(rule: dict[str, Any]) -> str:
    threshold = rule.get("threshold")
    descriptions = {
        "price_below": f"Price below {usd_text(threshold)} per hour",
        "price_change_pct": f"Price moves at least {threshold:g}%",
        "recommendation_change": "Rent-or-own recommendation changes",
        "savings_above": f"Modeled savings exceed {usd_text(threshold)}",
        "break_even_below": f"Break-even utilization falls below {threshold:g}%",
        "gpu_change": "Recommended GPU changes",
        "confidence_below": f"Confidence falls below {threshold:g}%",
    }
    return descriptions.get(rule["alert_type"], rule["alert_type"].replace("_", " "))


def usd_text(value: float | None) -> str:
    return "—" if value is None else f"${value:,.2f}"


def _target_hint(channel: str, target: str) -> str:
    if not target:
        return ""
    if channel == "email":
        local, _, domain = target.partition("@")
        return f"{local[:1]}***@{domain}"
    try:
        from urllib.parse import urlsplit

        return urlsplit(target).hostname or "configured webhook"
    except ValueError:
        return "configured webhook"


def _public_rule(rule: dict[str, Any]) -> dict[str, Any]:
    public = dict(rule)
    public.pop("delivery_secret", None)
    target = public.pop("delivery_target", "")
    public["delivery_target_hint"] = _target_hint(
        public.get("delivery_channel", "in_app"), target
    )
    if "latest_delivery" in public:
        raw_delivery = public["latest_delivery"]
        public["latest_delivery"] = {
            key: raw_delivery.get(key)
            for key in (
                "channel",
                "status",
                "attempts",
                "last_attempt_at",
                "delivered_at",
                "response_code",
            )
        }
        public["latest_delivery"]["target_hint"] = _target_hint(
            raw_delivery.get("channel", ""), raw_delivery.get("target", "")
        )
    return public


def _state_from_json(data: dict[str, Any]) -> alert_engine.AlertState:
    emitted = data.get("last_emitted_at")
    return alert_engine.AlertState(
        previous_value=data.get("previous_value"),
        pending_confirmations=data.get("pending_confirmations", 0),
        active=data.get("active", False),
        last_emitted_at=datetime.fromisoformat(emitted) if emitted else None,
        last_dedupe_key=data.get("last_dedupe_key"),
    )


def _state_to_json(state: alert_engine.AlertState) -> dict[str, Any]:
    data = asdict(state)
    if state.last_emitted_at is not None:
        data["last_emitted_at"] = state.last_emitted_at.isoformat()
    return data


def _domain_alert_rule(rule: dict[str, Any]) -> alert_engine.AlertRule:
    kind = rule["alert_type"]
    threshold = rule.get("threshold")
    if kind == "price_below":
        alert_type, metric, direction = (
            alert_engine.AlertType.THRESHOLD,
            "price_per_hour",
            alert_engine.AlertDirection.BELOW,
        )
    elif kind == "price_change_pct":
        alert_type, metric, direction = (
            alert_engine.AlertType.CHANGE,
            "price_per_hour",
            alert_engine.AlertDirection.CHANGES,
        )
        threshold = None if threshold is None else threshold / 100
    elif kind == "recommendation_change":
        alert_type, metric, direction = (
            alert_engine.AlertType.RECOMMENDATION,
            "recommendation",
            alert_engine.AlertDirection.CHANGES,
        )
    elif kind == "savings_above":
        alert_type, metric, direction = (
            alert_engine.AlertType.SAVINGS,
            "savings",
            alert_engine.AlertDirection.ABOVE,
        )
    elif kind == "break_even_below":
        alert_type, metric, direction = (
            alert_engine.AlertType.BREAK_EVEN,
            "break_even_utilization",
            alert_engine.AlertDirection.BELOW,
        )
        threshold = None if threshold is None else threshold / 100
    elif kind == "gpu_change":
        alert_type, metric, direction = (
            alert_engine.AlertType.GPU,
            "recommended_gpu",
            alert_engine.AlertDirection.CHANGES,
        )
    elif kind == "confidence_below":
        alert_type, metric, direction = (
            alert_engine.AlertType.CONFIDENCE,
            "confidence",
            alert_engine.AlertDirection.BELOW,
        )
        threshold = None if threshold is None else threshold / 100
    else:
        raise ValueError(f"unsupported alert type {kind!r}")
    return alert_engine.AlertRule(
        id=rule["id"],
        type=alert_type,
        metric=metric,
        direction=direction,
        threshold=threshold,
        confirmations=rule["required_observations"],
        cooldown=timedelta(hours=rule["cooldown_hours"]),
        relative_change=kind == "price_change_pct",
    )


def _alert_value(rule: dict[str, Any], latest_prices: list[dict[str, Any]]) -> float | str | None:
    gpu_prices = [
        row["price_per_hour"] for row in latest_prices if row["gpu"] == rule["gpu"]
    ]
    if not gpu_prices:
        return None
    latest_price = min(gpu_prices)
    if rule["alert_type"] in {"price_below", "price_change_pct"}:
        return latest_price
    if not rule.get("scenario"):
        return None
    scenario_request = ComputeRequest.model_validate(rule["scenario"])
    scenario_request.rental_prices[rule["gpu"]] = latest_price
    decision = compute(scenario_request)
    summary = decision["decision_summary"]
    if rule["alert_type"] == "recommendation_change":
        return f"{summary['gpu']}:{summary['option']}"
    if rule["alert_type"] == "savings_above":
        return summary["savings_vs_next_best"]
    if rule["alert_type"] == "gpu_change":
        return summary["gpu"]
    if rule["alert_type"] == "break_even_below":
        row = next(item for item in decision["results"] if item["name"] == rule["gpu"])
        return row["rent_vs_buy"]["break_even_utilization"]
    if rule["alert_type"] == "confidence_below":
        return 1.0
    return None


def evaluate_alert_rules(fetched_at: float | None = None) -> dict[str, Any]:
    """Evaluate active rules against one consistent latest price batch."""
    if fetched_at is None:
        latest_prices, fetched_at = price_store.latest_batch()
    else:
        latest_prices = price_store.batch_at(fetched_at)
    if not latest_prices or fetched_at is None:
        return {"evaluated": 0, "events": [], "reason": "no_market_snapshot"}
    observed_at = datetime.fromtimestamp(fetched_at, tz=UTC)
    events: list[dict[str, Any]] = []
    evaluated = 0
    for rule in intelligence_store.list_rules(active_only=True):
        if rule["state"].get("last_observed_at") == fetched_at:
            continue
        value = _alert_value(rule, latest_prices)
        if value is None:
            continue
        domain_rule = _domain_alert_rule(rule)
        state, event = alert_engine.evaluate_alert(
            domain_rule, _state_from_json(rule["state"]), value, observed_at
        )
        state_json = _state_to_json(state)
        state_json["last_observed_at"] = fetched_at
        event_payload = None
        if event is not None:
            previous = rule["state"].get("previous_value")
            event_payload = {
                "value": float(value) if isinstance(value, (float, int)) else None,
                "previous_value": (
                    float(previous) if isinstance(previous, (float, int)) else None
                ),
                "explanation": f"{_alert_description(rule)}: observed {value}.",
                "context": {"fetched_at": fetched_at},
                "dedupe_key": event.dedupe_key,
            }
        committed, created_event = intelligence_store.commit_evaluation(
            rule_id=rule["id"],
            previous_state=rule["state"],
            new_state=state_json,
            event=event_payload,
        )
        if not committed:
            continue  # another evaluator advanced this rule first
        evaluated += 1
        if created_event is not None:
            events.append(created_event)
    return {"evaluated": evaluated, "events": events, "fetched_at": fetched_at}


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


# --- Decision intelligence ------------------------------------------------------

@app.get("/api/data-health")
def data_health() -> dict[str, Any]:
    """Latest scheduled collection run and provider-level outcome ledger."""
    return price_store.data_health()


@app.get("/api/workloads")
def workload_catalog() -> dict[str, Any]:
    return {
        "registry_version": registry.REGISTRY_VERSION,
        "profiles": workloads.catalog(),
        "models": [asdict(model) for model in registry.MODELS.values()],
    }


@app.get("/api/registry")
def registry_catalog() -> dict[str, object]:
    """Auditable hardware, model, and source definitions."""
    return registry.catalog()


@app.post("/api/workloads/evaluate")
def evaluate_workload(req: WorkloadEvaluationRequest) -> dict[str, Any]:
    try:
        base = workloads.PROFILES[req.profile]
        profile = replace(
            base,
            model=req.model,
            input_tokens=req.average_input_tokens,
            output_tokens=req.average_output_tokens,
            concurrent_requests=max(
                1,
                ceil(
                    req.peak_requests_per_second * (req.latency_target_seconds or 1)
                ),
            ),
            max_latency_seconds=req.latency_target_seconds,
            capacity_headroom=req.capacity_headroom,
        )
        evaluations = workloads.evaluate(profile)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rows = []
    for evaluation in evaluations:
        row = asdict(evaluation)
        row["reason"] = evaluation.reason
        rows.append(row)
    return {
        "profile": asdict(profile),
        "evaluations": rows,
        "note": (
            "Published offline throughput is adjusted conservatively for this serving pattern. "
            "Measured and estimated benchmark provenance remains visible."
        ),
    }


@app.post("/api/backtests")
def run_backtest(req: BacktestRequest) -> dict[str, Any]:
    if req.gpu not in CANONICAL_GPUS:
        raise HTTPException(status_code=404, detail=f"unknown gpu {req.gpu!r}")
    gpu_input = next((gpu for gpu in req.scenario.gpus if gpu.name == req.gpu), None)
    if gpu_input is None:
        raise HTTPException(status_code=400, detail="scenario does not contain the selected GPU")

    decision_at = datetime.fromtimestamp(req.decision_at, tz=UTC)
    realized_end = decision_at + timedelta(hours=req.horizon_hours)
    max_age = timedelta(minutes=req.max_quote_age_minutes)
    history = price_store.history_between(
        req.gpu,
        req.decision_at - max_age.total_seconds(),
        realized_end.timestamp(),
    )
    if not history:
        raise HTTPException(
            status_code=422,
            detail="No stored market observations cover that decision window yet.",
        )
    if not any(row["fetched_at"] <= req.decision_at for row in history):
        raise HTTPException(
            status_code=422,
            detail="No market observation was available at the requested decision time.",
        )

    scenario = _build_scenario(gpu_input, req.scenario.datacenter, req.scenario.workload)
    owner_rate = cost_per_hour(scenario).total_per_provisioned_hour
    utilization = req.scenario.workload.utilization
    quotes: list[backtesting.HistoricalQuote] = []
    for row in history:
        observed = datetime.fromtimestamp(row["fetched_at"], tz=UTC)
        quotes.append(
            backtesting.HistoricalQuote(
                observed_at=observed,
                alternative=f"rent:{row['provider']}",
                hourly_cost=row["price_per_hour"] * utilization,
            )
        )
    # Ownership is continuously available at its modeled provisioned cost,
    # unlike rental observations which expire when market coverage has a gap.
    quotes.append(
        backtesting.HistoricalQuote(decision_at, "own", owner_rate, expires=False)
    )
    try:
        result = backtesting.backtest_decision(
            quotes,
            decision_at=decision_at,
            realized_start=decision_at,
            realized_end=realized_end,
            max_quote_age=max_age,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload = asdict(result)
    payload["points"] = [
        {
            "timestamp": point.start.timestamp(),
            "fixed_cost": point.chosen_hourly_cost,
            "hindsight_cost": point.hindsight_hourly_cost,
        }
        for point in result.time_series_points
    ]
    payload["coverage_note"] = (
        "The selected provider did not have a fresh observation for every interval."
        if result.incomplete
        else "Every interval is covered by a fresh market observation."
    )
    return payload


@app.get("/api/alerts")
def list_alerts() -> dict[str, Any]:
    rules = intelligence_store.list_rules()
    deliveries = intelligence_store.list_deliveries(limit=200)
    latest_delivery = {}
    for delivery in deliveries:
        latest_delivery.setdefault(delivery["rule_id"], delivery)
    for rule in rules:
        rule["description"] = _alert_description(rule)
        if rule["id"] in latest_delivery:
            rule["latest_delivery"] = latest_delivery[rule["id"]]
    return {
        "rules": [_public_rule(rule) for rule in rules],
        "events": intelligence_store.list_events(limit=20),
    }


@app.get("/api/alerts/delivery-capabilities")
def delivery_capabilities() -> dict[str, Any]:
    return {
        "in_app": True,
        "email": notifications.email_configured(),
        "webhook": True,
        "webhook_signing": "HMAC-SHA256",
        "external_delivery_configured": bool(ALERT_DELIVERY_TOKEN),
        "external_delivery_requires_token": True,
    }


@app.post("/api/alerts", status_code=201)
def create_alert(
    req: AlertRuleRequest,
    x_alert_token: str | None = Header(default=None),
) -> dict[str, Any]:
    if req.gpu not in CANONICAL_GPUS:
        raise HTTPException(status_code=404, detail=f"unknown gpu {req.gpu!r}")
    scenario_types = {
        "recommendation_change", "savings_above", "break_even_below", "gpu_change",
        "confidence_below",
    }
    if req.alert_type in scenario_types and req.scenario is None:
        raise HTTPException(status_code=400, detail="this trigger requires scenario assumptions")
    if req.alert_type not in {"recommendation_change", "gpu_change"} and req.threshold is None:
        raise HTTPException(status_code=400, detail="this trigger requires a threshold")
    channel = req.delivery_channel.strip().lower()
    target = req.delivery_target.strip()
    delivery_secret = ""
    if channel in {"email", "webhook"}:
        if not ALERT_DELIVERY_TOKEN:
            raise HTTPException(status_code=503, detail="external delivery is not configured")
        provided_token = x_alert_token if isinstance(x_alert_token, str) else ""
        if not secrets.compare_digest(provided_token, ALERT_DELIVERY_TOKEN):
            raise HTTPException(status_code=403, detail="invalid delivery access token")
        external_rules = sum(
            rule.get("delivery_channel") in {"email", "webhook"}
            for rule in intelligence_store.list_rules(active_only=True)
        )
        if external_rules >= 20:
            raise HTTPException(status_code=429, detail="external alert limit reached")
    if channel == "email":
        if not notifications.email_configured():
            raise HTTPException(status_code=503, detail="email delivery is not configured")
        try:
            target = notifications.validate_email(target)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    elif channel == "webhook":
        try:
            target = notifications.validate_webhook_url(target)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        delivery_secret = secrets.token_urlsafe(32)
    elif channel == "in_app":
        target = ""
    else:
        raise HTTPException(status_code=400, detail="unsupported delivery channel")
    candidate = {
        "id": "validation",
        "alert_type": req.alert_type,
        "threshold": req.threshold,
        "required_observations": req.required_observations,
        "cooldown_hours": req.cooldown_hours,
    }
    try:
        _domain_alert_rule(candidate)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rule = intelligence_store.create_rule(
        gpu=req.gpu,
        alert_type=req.alert_type,
        threshold=req.threshold,
        required_observations=req.required_observations,
        cooldown_hours=req.cooldown_hours,
        scenario=None if req.scenario is None else req.scenario.model_dump(),
        delivery_channel=channel,
        delivery_target=target,
        delivery_secret=delivery_secret,
    )
    rule["description"] = _alert_description(rule)
    public = _public_rule(rule)
    if delivery_secret:
        public["webhook_signing_secret"] = delivery_secret
    return public


@app.patch("/api/alerts/{rule_id}")
def update_alert(rule_id: str, req: AlertStatusRequest) -> dict[str, Any]:
    try:
        rule = intelligence_store.set_active(rule_id, req.active)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="alert rule not found") from exc
    rule["description"] = _alert_description(rule)
    return _public_rule(rule)


@app.post("/api/alerts/evaluate")
def evaluate_alerts() -> dict[str, Any]:
    """Evaluate in-app triggers; the scheduled collector calls the same function."""
    return evaluate_alert_rules()


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
