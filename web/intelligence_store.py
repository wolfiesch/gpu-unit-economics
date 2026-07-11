"""Persistence for decision triggers and their in-application event history."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from .store import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alert_rules (
    id TEXT PRIMARY KEY,
    gpu TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    threshold REAL,
    required_observations INTEGER NOT NULL DEFAULT 3,
    cooldown_hours REAL NOT NULL DEFAULT 24,
    active INTEGER NOT NULL DEFAULT 1,
    state_json TEXT NOT NULL DEFAULT '{}',
    scenario_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alert_rules_active ON alert_rules (active, gpu);

CREATE TABLE IF NOT EXISTS alert_events (
    id TEXT PRIMARY KEY,
    rule_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    value REAL,
    previous_value REAL,
    explanation TEXT NOT NULL,
    context_json TEXT NOT NULL DEFAULT '{}',
    dedupe_key TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (rule_id) REFERENCES alert_rules(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_alert_events_rule_time
    ON alert_events (rule_id, created_at DESC);
"""


class IntelligenceStore:
    """Small SQLite repository shared by API and scheduled collection commands."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(alert_rules)")}
            if "scenario_json" not in columns:
                conn.execute(
                    "ALTER TABLE alert_rules ADD COLUMN scenario_json TEXT NOT NULL DEFAULT '{}'"
                )
            event_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(alert_events)")
            }
            if "dedupe_key" not in event_columns:
                conn.execute(
                    "ALTER TABLE alert_events ADD COLUMN dedupe_key TEXT NOT NULL DEFAULT ''"
                )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_alert_events_rule_dedupe"
                " ON alert_events (rule_id, dedupe_key) WHERE dedupe_key != ''"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        if "active" in data:
            data["active"] = bool(data["active"])
        if "state_json" in data:
            data["state"] = json.loads(data.pop("state_json") or "{}")
        if "scenario_json" in data:
            data["scenario"] = json.loads(data.pop("scenario_json") or "{}")
        if "context_json" in data:
            data["context"] = json.loads(data.pop("context_json") or "{}")
        return data

    def create_rule(
        self,
        *,
        gpu: str,
        alert_type: str,
        threshold: float | None,
        required_observations: int = 3,
        cooldown_hours: float = 24,
        scenario: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        rule_id = uuid.uuid4().hex[:16]
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO alert_rules"
                " (id, gpu, alert_type, threshold, required_observations, cooldown_hours,"
                " active, state_json, scenario_json, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 1, '{}', ?, ?, ?)",
                (
                    rule_id,
                    gpu,
                    alert_type,
                    threshold,
                    required_observations,
                    cooldown_hours,
                    json.dumps(scenario or {}, separators=(",", ":"), sort_keys=True),
                    now,
                    now,
                ),
            )
        return self.get_rule(rule_id)

    def get_rule(self, rule_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM alert_rules WHERE id = ?", (rule_id,)).fetchone()
        if row is None:
            raise KeyError(rule_id)
        return self._row(row)

    def list_rules(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM alert_rules"
        params: tuple[Any, ...] = ()
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row(row) for row in rows]

    def set_active(self, rule_id: str, active: bool) -> dict[str, Any]:
        with self._connect() as conn:
            result = conn.execute(
                "UPDATE alert_rules SET active = ?, updated_at = ? WHERE id = ?",
                (int(active), time.time(), rule_id),
            )
        if result.rowcount == 0:
            raise KeyError(rule_id)
        return self.get_rule(rule_id)

    def save_state(self, rule_id: str, state: dict[str, Any]) -> None:
        with self._connect() as conn:
            result = conn.execute(
                "UPDATE alert_rules SET state_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(state, separators=(",", ":"), sort_keys=True), time.time(), rule_id),
            )
        if result.rowcount == 0:
            raise KeyError(rule_id)

    def add_event(
        self,
        *,
        rule_id: str,
        value: float | None,
        previous_value: float | None,
        explanation: str,
        context: dict[str, Any] | None = None,
        dedupe_key: str = "",
    ) -> dict[str, Any]:
        event_id = uuid.uuid4().hex[:16]
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO alert_events"
                " (id, rule_id, created_at, value, previous_value, explanation,"
                " context_json, dedupe_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    rule_id,
                    time.time(),
                    value,
                    previous_value,
                    explanation,
                    json.dumps(context or {}, separators=(",", ":"), sort_keys=True),
                    dedupe_key,
                ),
            )
            row = conn.execute("SELECT * FROM alert_events WHERE id = ?", (event_id,)).fetchone()
        assert row is not None
        return self._row(row)

    def commit_evaluation(
        self,
        *,
        rule_id: str,
        previous_state: dict[str, Any],
        new_state: dict[str, Any],
        event: dict[str, Any] | None = None,
    ) -> tuple[bool, dict[str, Any] | None]:
        """Atomically compare-and-swap rule state and optionally create an event."""
        previous_json = json.dumps(previous_state, separators=(",", ":"), sort_keys=True)
        new_json = json.dumps(new_state, separators=(",", ":"), sort_keys=True)
        created: dict[str, Any] | None = None
        with self._connect() as conn:
            updated = conn.execute(
                "UPDATE alert_rules SET state_json = ?, updated_at = ?"
                " WHERE id = ? AND state_json = ?",
                (new_json, time.time(), rule_id, previous_json),
            )
            if updated.rowcount == 0:
                return False, None
            if event is not None:
                event_id = uuid.uuid4().hex[:16]
                inserted = conn.execute(
                    "INSERT OR IGNORE INTO alert_events"
                    " (id, rule_id, created_at, value, previous_value, explanation,"
                    " context_json, dedupe_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event_id,
                        rule_id,
                        time.time(),
                        event.get("value"),
                        event.get("previous_value"),
                        event["explanation"],
                        json.dumps(
                            event.get("context") or {}, separators=(",", ":"), sort_keys=True
                        ),
                        event["dedupe_key"],
                    ),
                )
                if inserted.rowcount:
                    row = conn.execute(
                        "SELECT * FROM alert_events WHERE id = ?", (event_id,)
                    ).fetchone()
                    assert row is not None
                    created = self._row(row)
        return True, created

    def list_events(self, *, rule_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 200))
        query = "SELECT * FROM alert_events"
        params: tuple[Any, ...]
        if rule_id:
            query += " WHERE rule_id = ?"
            params = (rule_id, limit)
        else:
            params = (limit,)
        query += " ORDER BY created_at DESC LIMIT ?"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row(row) for row in rows]
