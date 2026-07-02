"""SQLite-backed price snapshot store with a fetch-through TTL cache.

Every successful provider fetch is appended as a snapshot row, giving free
price history. `get_latest` serves cached rows while fresh, refetching from
providers only when the newest snapshot is older than the TTL. On upstream
failure it degrades to the last known snapshot (stale-while-error) so the
dashboard keeps working offline.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .providers import PriceQuote, fetch_all

DEFAULT_TTL_S = int(os.environ.get("PRICE_TTL_SECONDS", 15 * 60))
DB_PATH = Path(os.environ.get("PRICE_DB_PATH", Path(__file__).resolve().parent / "prices.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS price_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at REAL NOT NULL,           -- unix epoch seconds
    provider TEXT NOT NULL,
    gpu TEXT NOT NULL,
    price_per_hour REAL NOT NULL,
    kind TEXT NOT NULL,
    source_url TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_snapshots_time ON price_snapshots (fetched_at);
CREATE INDEX IF NOT EXISTS idx_snapshots_gpu ON price_snapshots (gpu, provider, fetched_at);
"""


class PriceStore:
    def __init__(self, db_path: Path = DB_PATH, ttl_s: int = DEFAULT_TTL_S) -> None:
        self.db_path = db_path
        self.ttl_s = ttl_s
        self._refresh_lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # --- Reads -------------------------------------------------------------

    def latest_batch(self) -> tuple[list[dict[str, Any]], float | None]:
        """Rows from the most recent fetch batch and its timestamp."""
        with self._connect() as conn:
            row = conn.execute("SELECT MAX(fetched_at) AS t FROM price_snapshots").fetchone()
            if row is None or row["t"] is None:
                return [], None
            batch_time = row["t"]
            rows = conn.execute(
                "SELECT provider, gpu, price_per_hour, kind, source_url, detail"
                " FROM price_snapshots WHERE fetched_at = ?",
                (batch_time,),
            ).fetchall()
            return [dict(r) for r in rows], batch_time

    def history(self, gpu: str, hours: float = 24 * 7) -> list[dict[str, Any]]:
        """Snapshot rows for one canonical GPU within the trailing window."""
        cutoff = time.time() - hours * 3600
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT fetched_at, provider, price_per_hour, kind"
                " FROM price_snapshots WHERE gpu = ? AND fetched_at >= ?"
                " ORDER BY fetched_at",
                (gpu, cutoff),
            ).fetchall()
            return [dict(r) for r in rows]

    # --- Fetch-through cache -------------------------------------------------

    def get_latest(self, force: bool = False) -> dict[str, Any]:
        """Latest prices, refreshing from providers if the cache is stale.

        Response always states its provenance: `fetched_at`, `stale`, and any
        provider errors from the most recent refresh attempt.
        """
        rows, batch_time = self.latest_batch()
        age = None if batch_time is None else time.time() - batch_time
        errors: list[str] = []

        if force or age is None or age > self.ttl_s:
            with self._refresh_lock:
                # Double-check under the lock: another request may have refreshed.
                rows, batch_time = self.latest_batch()
                age = None if batch_time is None else time.time() - batch_time
                if force or age is None or age > self.ttl_s:
                    quotes, errors = fetch_all()
                    if quotes:
                        batch_time = self._append(quotes)
                        rows = [q.to_dict() for q in quotes]
                        age = 0.0

        return {
            "prices": rows,
            "fetched_at": batch_time,
            "age_seconds": None if age is None else round(age),
            "ttl_seconds": self.ttl_s,
            "stale": age is None or age > self.ttl_s,
            "errors": errors,
        }

    def _append(self, quotes: list[PriceQuote]) -> float:
        now = time.time()
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO price_snapshots"
                " (fetched_at, provider, gpu, price_per_hour, kind, source_url, detail)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (now, q.provider, q.gpu, q.price_per_hour, q.kind, q.source_url, q.detail)
                    for q in quotes
                ],
            )
        return now
