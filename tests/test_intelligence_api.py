from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest
from web.intelligence_store import IntelligenceStore
from web.store import PriceStore

app_module = importlib.import_module("web.app")


def compute_request() -> app_module.ComputeRequest:
    return app_module.ComputeRequest(
        gpus=[
            app_module.GpuInput(
                name="H100", capex_usd=30_000, power_kw=0.7, tokens_per_sec=3_000
            )
        ]
    )


def insert_price(db_path: Path, fetched_at: float, price: float = 2.0) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO price_snapshots"
            " (fetched_at, provider, gpu, price_per_hour, kind, source_url, detail, region)"
            " VALUES (?, 'runpod', 'H100', ?, 'secure', 'https://example.test', '', '')",
            (fetched_at, price),
        )


def test_workload_endpoint_returns_explainable_gpu_evaluations() -> None:
    payload = app_module.evaluate_workload(
        app_module.WorkloadEvaluationRequest(profile="interactive", model="llama-3.1-8b")
    )

    assert [row["gpu"] for row in payload["evaluations"]] == ["H100", "H200", "B200"]
    assert all("provenance" in row and "reason" in row for row in payload["evaluations"])


def test_backtest_endpoint_uses_absolute_history_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = PriceStore(tmp_path / "prices.db")
    decision_at = 1_700_000_000.0
    for offset, price in ((-60, 2.0), (600, 1.9), (1_200, 1.8)):
        insert_price(store.db_path, decision_at + offset, price)
    monkeypatch.setattr(app_module, "price_store", store)

    result = app_module.run_backtest(
        app_module.BacktestRequest(
            gpu="H100",
            decision_at=decision_at,
            horizon_hours=0.5,
            max_quote_age_minutes=15,
            scenario=compute_request(),
        )
    )

    assert result["coverage"] == 1
    assert result["incomplete"] is False
    assert result["original_option"] in {"own", "rent:runpod"}
    assert result["points"]


def test_price_alert_persists_state_and_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    price_store = PriceStore(tmp_path / "prices.db")
    intelligence_store = IntelligenceStore(price_store.db_path)
    insert_price(price_store.db_path, 1_700_000_000, 1.8)
    monkeypatch.setattr(app_module, "price_store", price_store)
    monkeypatch.setattr(app_module, "intelligence_store", intelligence_store)

    rule = app_module.create_alert(
        app_module.AlertRuleRequest(
            gpu="H100",
            alert_type="price_below",
            threshold=2.0,
            required_observations=1,
        )
    )
    evaluation = app_module.evaluate_alert_rules()

    assert evaluation["evaluated"] == 1
    assert len(evaluation["events"]) == 1
    assert intelligence_store.get_rule(rule["id"])["state"]["active"] is True

    repeated = app_module.evaluate_alert_rules()
    assert repeated["evaluated"] == 0
    assert repeated["events"] == []
