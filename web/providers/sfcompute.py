"""SF Compute orderbook quotes via the authenticated preview API."""

from __future__ import annotations

import os
import time

from . import PriceQuote, http_json

PROVIDER = "sfcompute"
SOURCE_URL = "https://sfcompute.com/prices"
API = "https://api.sfcompute.com/preview/v2/orderbook/quote"
ENV_KEY = "SFCOMPUTE_API_KEY"
H100_GPUS_PER_NODE = 8


def fetch() -> list[PriceQuote]:
    key = os.environ.get(ENV_KEY)
    if not key:
        return []

    now = int(time.time())
    query = f"requirements=accelerator:H100&start_at={now + 3600}&end_at={now + 7200}"
    payload = http_json(f"{API}?{query}", headers={"Authorization": f"Bearer {key}"})
    best_ask = payload.get("best_ask") if isinstance(payload, dict) else None
    if not isinstance(best_ask, dict):
        return []

    try:
        node_price = float(best_ask.get("dollars_per_node_hour"))
    except (TypeError, ValueError):
        return []
    if node_price <= 0:
        return []

    return [
        PriceQuote(
            provider=PROVIDER,
            gpu="H100",
            price_per_hour=round(node_price / H100_GPUS_PER_NODE, 4),
            kind="spot",
            source_url=SOURCE_URL,
            detail="orderbook best ask, per-node/8",
        )
    ]
