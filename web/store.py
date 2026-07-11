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
import uuid
from pathlib import Path
from typing import Any

from .providers import PriceQuote, fetch_all

DEFAULT_TTL_S = int(os.environ.get("PRICE_TTL_SECONDS", 15 * 60))
COLLECTION_RUN_TIMEOUT_S = int(os.environ.get("COLLECTION_RUN_TIMEOUT_SECONDS", 5 * 60))
DB_PATH = Path(os.environ.get("PRICE_DB_PATH", Path(__file__).resolve().parent / "prices.db"))
# Retention: keep every row for this many days, then thin old batches to one
# cheapest row per (gpu, provider, region) per hour bucket.
RETENTION_FULL_DAYS = float(os.environ.get("PRICE_RETENTION_FULL_DAYS", 30))
PRUNE_INTERVAL_S = 24 * 3600

_SCHEMA = """
CREATE TABLE IF NOT EXISTS price_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at REAL NOT NULL,           -- unix epoch seconds
    provider TEXT NOT NULL,
    gpu TEXT NOT NULL,
    price_per_hour REAL NOT NULL,
    kind TEXT NOT NULL,
    source_url TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    region TEXT NOT NULL DEFAULT '',
    run_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_snapshots_time ON price_snapshots (fetched_at);
CREATE INDEX IF NOT EXISTS idx_snapshots_gpu ON price_snapshots (gpu, provider, fetched_at);
CREATE TABLE IF NOT EXISTS collection_runs (
    id TEXT PRIMARY KEY,
    started_at REAL NOT NULL,
    finished_at REAL,
    status TEXT NOT NULL CHECK (status IN ('running', 'success', 'partial', 'failed')),
    trigger TEXT NOT NULL DEFAULT 'scheduled',
    quote_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_running_collection
    ON collection_runs (status) WHERE status = 'running';
CREATE TABLE IF NOT EXISTS provider_run_results (
    run_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('success', 'failed')),
    quote_count INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (run_id, provider),
    FOREIGN KEY (run_id) REFERENCES collection_runs(id) ON DELETE CASCADE
);
"""


class PriceStore:
    def __init__(self, db_path: Path = DB_PATH, ttl_s: int = DEFAULT_TTL_S) -> None:
        self.db_path = db_path
        self.ttl_s = ttl_s
        self._refresh_lock = threading.Lock()
        self._last_prune = 0.0
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Bring pre-existing databases up to the current schema.

        CREATE TABLE IF NOT EXISTS never alters an existing table, so columns
        added later must be back-filled here. Old rows keep region='' and stay
        fully readable.
        """
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(price_snapshots)")}
        if "region" not in cols:
            conn.execute(
                "ALTER TABLE price_snapshots ADD COLUMN region TEXT NOT NULL DEFAULT ''"
            )
        if "run_id" not in cols:
            conn.execute("ALTER TABLE price_snapshots ADD COLUMN run_id TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_snapshots_run ON price_snapshots (run_id)"
        )

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
                "SELECT provider, gpu, price_per_hour, kind, source_url, detail, region"
                " FROM price_snapshots WHERE fetched_at = ?",
                (batch_time,),
            ).fetchall()
            return [dict(r) for r in rows], batch_time

    def batch_at(self, fetched_at: float) -> list[dict[str, Any]]:
        """Return one exact snapshot batch for deterministic trigger evaluation."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT provider, gpu, price_per_hour, kind, source_url, detail, region"
                " FROM price_snapshots WHERE fetched_at = ?",
                (fetched_at,),
            ).fetchall()
        return [dict(row) for row in rows]

    def history(self, gpu: str, hours: float = 24 * 7) -> list[dict[str, Any]]:
        """Cheapest price per (batch, provider) for one GPU in the window.

        Batches carry every regional quote since the region column landed;
        collapsing to the per-provider minimum keeps the chart a price line
        instead of a vertical zig-zag through all regions. Only the fields the
        chart consumes are returned — per-region metadata lives in
        spread_history / the regions endpoint.
        """
        cutoff = time.time() - hours * 3600
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT fetched_at, provider, MIN(price_per_hour) AS price_per_hour"
                " FROM price_snapshots WHERE gpu = ? AND fetched_at >= ?"
                " GROUP BY fetched_at, provider ORDER BY fetched_at",
                (gpu, cutoff),
            ).fetchall()
            return [dict(r) for r in rows]

    def history_between(
        self, gpu: str, start_at: float, end_at: float
    ) -> list[dict[str, Any]]:
        """Cheapest provider prices inside an absolute time window.

        Backtests need explicit historical boundaries rather than a trailing
        window based on the current clock. Keeping this query in the store also
        makes the no-future-data rule straightforward to test.
        """
        if end_at <= start_at:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT fetched_at, provider, MIN(price_per_hour) AS price_per_hour"
                " FROM price_snapshots WHERE gpu = ? AND fetched_at >= ? AND fetched_at <= ?"
                " GROUP BY fetched_at, provider ORDER BY fetched_at, provider",
                (gpu, start_at, end_at),
            ).fetchall()
        return [dict(row) for row in rows]

    def spread_history(self, gpu: str, hours: float = 24 * 30) -> list[dict[str, Any]]:
        """Per-batch min/max/count across regions for one GPU (spread over time).

        Only rows with a known region participate; batches predating regional
        capture (region='') contribute nothing rather than a fake 0 spread.
        """
        cutoff = time.time() - hours * 3600
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT fetched_at, MIN(price_per_hour) AS min_price,"
                " MAX(price_per_hour) AS max_price, COUNT(*) AS regions"
                " FROM price_snapshots"
                " WHERE gpu = ? AND fetched_at >= ? AND region != ''"
                " GROUP BY fetched_at ORDER BY fetched_at",
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
                    from .collector import collect_prices

                    collected = collect_prices(self, trigger="request", fetcher=fetch_all)
                    run = collected.get("run") or {}
                    errors = run.get("error", "").splitlines()
                    if run.get("quote_count", 0):
                        rows, batch_time = self.latest_batch()
                        age = 0.0
                        # Opportunistic retention: at most once per day, thin
                        # rows past the full-resolution window.
                        if time.time() - self._last_prune > PRUNE_INTERVAL_S:
                            self._last_prune = time.time()
                            try:
                                self.prune()
                            except sqlite3.Error:
                                pass  # retention is best-effort; never break reads

        return {
            "prices": rows,
            "fetched_at": batch_time,
            "age_seconds": None if age is None else round(age),
            "ttl_seconds": self.ttl_s,
            "stale": age is None or age > self.ttl_s,
            "errors": errors,
        }

    def _append(self, quotes: list[PriceQuote], run_id: str | None = None) -> float:
        now = time.time()
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO price_snapshots"
                " (fetched_at, provider, gpu, price_per_hour, kind, source_url,"
                " detail, region, run_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        now, q.provider, q.gpu, q.price_per_hour,
                        q.kind, q.source_url, q.detail, q.region, run_id,
                    )
                    for q in quotes
                ],
            )
        return now

    # --- Collection lineage -------------------------------------------------

    def begin_collection_run(self, trigger: str = "scheduled") -> str | None:
        """Claim the single collection slot, returning its run ID.

        SQLite's partial unique index makes this safe across processes, not
        merely across threads in one web worker.
        """
        run_id = uuid.uuid4().hex
        try:
            with self._connect() as conn:
                now = time.time()
                conn.execute(
                    "UPDATE collection_runs SET finished_at=?, status='failed', error_count=1,"
                    " error='collection lease expired'"
                    " WHERE status='running' AND started_at < ?",
                    (now, now - COLLECTION_RUN_TIMEOUT_S),
                )
                conn.execute(
                    "INSERT INTO collection_runs (id, started_at, status, trigger)"
                    " VALUES (?, ?, 'running', ?)",
                    (run_id, now, trigger),
                )
        except sqlite3.IntegrityError:
            return None
        return run_id

    def finish_collection_run(
        self,
        run_id: str,
        quotes: list[PriceQuote],
        errors: list[str],
        provider_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Atomically store quotes, provider outcomes, and the final run state."""
        status = "partial" if quotes and errors else "success" if quotes else "failed"
        finished_at = time.time()
        fetched_at = finished_at if quotes else None
        with self._connect() as conn:
            if quotes:
                conn.executemany(
                    "INSERT INTO price_snapshots"
                    " (fetched_at, provider, gpu, price_per_hour, kind, source_url,"
                    " detail, region, run_id)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            fetched_at, q.provider, q.gpu, q.price_per_hour, q.kind,
                            q.source_url, q.detail, q.region, run_id,
                        )
                        for q in quotes
                    ],
                )
            conn.executemany(
                "INSERT INTO provider_run_results"
                " (run_id, provider, status, quote_count, error) VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        run_id, result["provider"], result["status"],
                        result.get("quote_count", 0), result.get("error", ""),
                    )
                    for result in provider_results
                ],
            )
            conn.execute(
                "UPDATE collection_runs SET finished_at=?, status=?, quote_count=?,"
                " error_count=?, error=? WHERE id=? AND status='running'",
                (finished_at, status, len(quotes), len(errors), "\n".join(errors), run_id),
            )
        return self.get_collection_run(run_id)

    def fail_collection_run(self, run_id: str, error: str) -> dict[str, Any]:
        """Close a run when the collector itself fails before normal results."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE collection_runs SET finished_at=?, status='failed', error_count=1,"
                " error=? WHERE id=? AND status='running'",
                (time.time(), error, run_id),
            )
        return self.get_collection_run(run_id)

    def get_collection_run(self, run_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM collection_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            result = dict(row)
            result["providers"] = [
                dict(item)
                for item in conn.execute(
                    "SELECT provider, status, quote_count, error"
                    " FROM provider_run_results WHERE run_id=? ORDER BY provider",
                    (run_id,),
                )
            ]
            return result

    def collection_health(self) -> dict[str, Any]:
        """Small operational summary suitable for a health endpoint or CLI."""
        with self._connect() as conn:
            running = conn.execute(
                "SELECT id, started_at, trigger FROM collection_runs"
                " WHERE status='running' LIMIT 1"
            ).fetchone()
            latest = conn.execute(
                "SELECT id FROM collection_runs WHERE status!='running'"
                " ORDER BY finished_at DESC LIMIT 1"
            ).fetchone()
            snapshot = conn.execute(
                "SELECT MAX(fetched_at) AS fetched_at, COUNT(*) AS total_snapshots"
                " FROM price_snapshots"
            ).fetchone()
        return {
            "running": None if running is None else dict(running),
            "latest_run": None if latest is None else self.get_collection_run(latest["id"]),
            "latest_snapshot_at": snapshot["fetched_at"],
            "total_snapshots": snapshot["total_snapshots"],
        }

    def data_health(self) -> dict[str, Any]:
        """Summarize whether the most recent collection produced trustworthy data."""
        health = self.collection_health()
        latest = health["latest_run"]
        if latest is None:
            return {
                "status": "collecting" if health["running"] else "no_data",
                "latest_run": None,
                "providers": [],
                "running": health["running"],
            }

        providers = latest.pop("providers")
        successful = sum(item["status"] == "success" for item in providers)
        latest["expected_providers"] = len(providers)
        latest["successful_providers"] = successful
        status = {
            "success": "healthy",
            "partial": "degraded",
            "failed": "unhealthy",
        }[latest["status"]]
        finished_at = latest.get("finished_at")
        if finished_at is not None and time.time() - finished_at > self.ttl_s * 2:
            status = "stale"
        if health["running"] is not None:
            status = "collecting"
        return {
            "status": status,
            "latest_run": latest,
            "providers": providers,
            "running": health["running"],
        }

    def prune(self, full_days: float = RETENTION_FULL_DAYS) -> int:
        """Thin rows older than the full-resolution window to hourly minima.

        Within each hour bucket, the cheapest row per (gpu, provider, region)
        survives; the rest are deleted. Spread and history charts keep their
        shape at hourly granularity while the table stops growing unbounded
        under sub-hourly polling. Returns the number of rows deleted.
        """
        cutoff = time.time() - full_days * 24 * 3600
        with self._connect() as conn:
            cur = conn.execute(
                """
                DELETE FROM price_snapshots WHERE id NOT IN (
                    SELECT id FROM (
                        SELECT id, MIN(price_per_hour)
                        FROM price_snapshots
                        WHERE fetched_at < :cutoff
                        GROUP BY CAST(fetched_at / 3600 AS INTEGER), gpu, provider, region
                    )
                ) AND fetched_at < :cutoff
                """,
                {"cutoff": cutoff},
            )
            conn.execute("PRAGMA optimize")
            return cur.rowcount
