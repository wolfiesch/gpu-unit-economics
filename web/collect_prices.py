"""One-shot scheduled market collection command.

Run with ``python -m web.collect_prices`` from a systemd timer or cron. The
command exits after one collection, so scheduling and restart policy remain the
operating system's responsibility rather than a hidden loop inside FastAPI.
"""

from __future__ import annotations

import json

from .collector import Collector
from .intelligence_store import IntelligenceStore
from .store import PriceStore


def run_once(store: PriceStore | None = None) -> dict:
    price_store = store or PriceStore()
    result = Collector(price_store).run(trigger="scheduled")
    if result.get("started") and result.get("run", {}).get("quote_count", 0):
        # Reuse the API's evaluation coordinator while pointing it at this
        # command's exact database. Importing here keeps provider collection
        # usable without FastAPI in tests and other scripts.
        from . import app as app_module

        app_module.price_store = price_store
        app_module.intelligence_store = IntelligenceStore(price_store.db_path)
        result["alerts"] = app_module.evaluate_alert_rules(
            fetched_at=result["run"]["finished_at"]
        )
    return result


def main() -> int:
    result = run_once()
    print(json.dumps(result, sort_keys=True, default=str))
    run = result.get("run") or {}
    return 1 if result.get("started") and run.get("status") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
