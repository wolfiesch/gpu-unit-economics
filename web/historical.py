"""Historical GPU price dataset loader.

Reads the static, audited ``data/historical_gpu_prices.csv`` once and caches it
in-process forever — the file is baked per deploy, so there is no TTL or network
access here (unlike ``web/power.py``). Only the columns the API exposes are
emitted; ``nominal_price`` is deliberately dropped because it carries reported
ranges (e.g. ``60000-70000``, ``199000/8``) that are not machine-parseable.
"""

from __future__ import annotations

import csv
import threading
from pathlib import Path
from typing import Any

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "historical_gpu_prices.csv"

TRACKS = ("current_ai_sku", "enterprise_pre_llm", "consumer_crypto_proxy")

CPI_BASE = {"month": "2026-05-01", "cpi": 333.979}
METHOD_NOTE = (
    "usd_2026 is general CPI-normalized (FRED CPIAUCSL), not hardware-deflated cost."
)
GAP_NOTE = (
    "No standalone B200 card price exists; GB200 rows are system/superchip capex "
    "allocations, not B200 card prices."
)

# Fields exposed per row, in order. nominal_price is intentionally omitted.
_FIELDS = (
    "sku",
    "vendor",
    "track",
    "market_segment",
    "price_type",
    "condition",
    "date",
    "period_label",
    "source_id",
    "confidence",
    "notes",
)

_cache: dict[str, Any] = {"rows": None, "skipped": 0}
_lock = threading.Lock()


def load_rows() -> list[dict[str, Any]]:
    """Return every usable price observation, cached forever after first read."""
    with _lock:
        if _cache["rows"] is not None:
            return _cache["rows"]

        rows: list[dict[str, Any]] = []
        skipped = 0
        with DATA_PATH.open(newline="", encoding="utf-8") as fh:
            for raw in csv.DictReader(fh):
                try:
                    usd_nominal = float(raw["usd_nominal"])
                except (TypeError, ValueError):
                    skipped += 1
                    continue

                usd_2026_raw = (raw.get("usd_2026") or "").strip()
                usd_2026: float | None
                if usd_2026_raw:
                    try:
                        usd_2026 = float(usd_2026_raw)
                    except ValueError:
                        usd_2026 = None
                else:
                    usd_2026 = None

                row = {field: raw.get(field, "") for field in _FIELDS}
                row["usd_nominal"] = usd_nominal
                row["usd_2026"] = usd_2026
                rows.append(row)

        _cache["rows"] = rows
        _cache["skipped"] = skipped
    return rows


def table() -> dict[str, Any]:
    """API payload: rows plus track enum and CPI/gap provenance notes."""
    rows = load_rows()
    return {
        "rows": rows,
        "tracks": list(TRACKS),
        "cpi_base": dict(CPI_BASE),
        "method_note": METHOD_NOTE,
        "gap_note": GAP_NOTE,
    }
