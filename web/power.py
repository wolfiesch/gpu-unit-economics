"""US industrial electricity prices by state, from the EIA v2 API.

Requires EIA_API_KEY (free key from https://www.eia.gov/opendata/). Fetched
server-side and cached in-process for a day — EIA data is monthly, so anything
fresher is wasted calls. Returns $/kWh (EIA reports cents/kWh).
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

from .providers import http_json

EIA_API_KEY = os.environ.get("EIA_API_KEY")
API = "https://api.eia.gov/v2/electricity/retail-sales/data/"
SOURCE_URL = "https://www.eia.gov/electricity/data.php"
CACHE_TTL_S = 24 * 3600

_cache: dict[str, Any] = {"fetched_at": 0.0, "data": None}
_lock = threading.Lock()


def available() -> bool:
    return bool(EIA_API_KEY)


def fetch_state_prices() -> dict[str, Any]:
    """Latest monthly industrial $/kWh for every US state, cached for a day."""
    now = time.time()
    with _lock:
        if _cache["data"] is not None and now - _cache["fetched_at"] < CACHE_TTL_S:
            return _cache["data"]

    url = (
        f"{API}?api_key={EIA_API_KEY}&frequency=monthly&data[0]=price"
        "&facets[sectorid][]=IND"
        "&sort[0][column]=period&sort[0][direction]=desc&length=120"
    )
    rows = http_json(url)["response"]["data"]

    # Rows are sorted newest-first; keep the first (latest) entry per state.
    latest_period = rows[0]["period"] if rows else None
    states: dict[str, dict[str, Any]] = {}
    for r in rows:
        state = r.get("stateid", "")
        price = r.get("price")
        if len(state) != 2 or state == "US" or price is None or state in states:
            continue  # skip aggregates (e.g. "US", census regions) and dupes
        states[state] = {
            "state": state,
            "name": r.get("stateDescription", state),
            "usd_per_kwh": round(float(price) / 100, 4),
            "period": r.get("period"),
        }

    data = {
        "period": latest_period,
        "source_url": SOURCE_URL,
        "states": sorted(states.values(), key=lambda s: s["state"]),
    }
    with _lock:
        _cache["fetched_at"] = now
        _cache["data"] = data
    return data
