"""OpenRouter token prices for the implied-inference-margin comparison.

Public, keyless JSON (``GET https://openrouter.ai/api/v1/models``) whose
``data[].pricing.prompt`` / ``.completion`` are USD-per-token decimal strings.
Cached in-process for a day (prices move slowly and the card is a comparison
aid, not a trading feed), following the ``web/power.py`` cached-fetch pattern.

Only open-weights families are kept: the "cost to serve vs. market price"
comparison is only coherent for models an operator could actually host.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from .providers import http_json

API = "https://openrouter.ai/api/v1/models"
SOURCE_URL = "https://openrouter.ai/models"
CACHE_TTL_S = 24 * 3600
MAX_ROWS = 25

# Open-weights families an operator could self-host; the comparison is only
# coherent for these (closed models have no "cost to serve" analogue here).
OPEN_WEIGHT_MARKERS = ("llama-3", "llama-2", "deepseek", "qwen", "mistral", "gpt-oss")

_cache: dict[str, Any] = {"fetched_at": 0.0, "data": None}
_lock = threading.Lock()


def _is_open_weight(model_id: str) -> bool:
    lowered = model_id.lower()
    return any(marker in lowered for marker in OPEN_WEIGHT_MARKERS)


def fetch_token_prices() -> dict[str, Any]:
    """Curated open-weights token prices, cheapest per id, cached for a day."""
    now = time.time()
    with _lock:
        if _cache["data"] is not None and now - _cache["fetched_at"] < CACHE_TTL_S:
            return _cache["data"]

    payload = http_json(API)
    raw_models = payload.get("data", [])

    # Cheapest offering per exact id (dedupe id duplicates, keep the cheaper).
    by_id: dict[str, dict[str, Any]] = {}
    for model in raw_models:
        model_id = model.get("id", "")
        if not model_id or not _is_open_weight(model_id):
            continue
        pricing = model.get("pricing") or {}
        try:
            out_per_token = float(pricing.get("completion"))
            in_per_token = float(pricing.get("prompt"))
        except (TypeError, ValueError):
            continue
        if out_per_token <= 0:
            continue
        row = {
            "id": model_id,
            "name": model.get("name", model_id),
            "usd_per_million_input": in_per_token * 1_000_000,
            "usd_per_million_output": out_per_token * 1_000_000,
        }
        existing = by_id.get(model_id)
        if existing is None or row["usd_per_million_output"] < existing["usd_per_million_output"]:
            by_id[model_id] = row

    models = sorted(by_id.values(), key=lambda m: m["usd_per_million_output"])[:MAX_ROWS]

    data = {
        "fetched_at": now,
        "source_url": SOURCE_URL,
        "models": models,
    }
    with _lock:
        _cache["fetched_at"] = now
        _cache["data"] = data
    return data
