"""ComputePrices aggregator rates from its public keyless GPU prices API.

This source is an aggregator: prices are other providers' rates re-published
by ComputePrices. Regions are usually absent, so these quotes appear in the
price table but generally not on the regional price map.
"""

from __future__ import annotations

from . import PriceQuote, http_json

PROVIDER = "computeprices"
SOURCE_URL = "https://computeprices.com"
API = "https://computeprices.com/api/v1/gpu-prices"

# Minimum plausible on-demand USD per GPU-hour. This drops serverless or
# fractional junk listings (for example Modal H100 at $0.066/hr).
SANITY_FLOOR = {"H100": 0.5, "H200": 0.6, "B200": 1.0}


def fetch() -> list[PriceQuote]:
    payload = http_json(API)
    rows = payload["data"] if isinstance(payload, dict) and "data" in payload else payload

    best: dict[tuple[str, str], PriceQuote] = {}
    for row in rows:
        slug = (row.get("gpu_slug") or "").upper()
        if slug not in SANITY_FLOOR:
            continue
        gpu = slug
        if row.get("pricing_type") != "on_demand":
            continue
        try:
            price = float(row.get("price_per_hour_usd"))
        except (TypeError, ValueError):
            continue
        if price < SANITY_FLOOR[gpu]:
            continue

        provider_name = row.get("provider") or "unknown"
        key = (gpu, provider_name)
        source_url = row.get("source_url") or SOURCE_URL
        if not source_url.startswith("https"):
            source_url = SOURCE_URL

        if key not in best or price < best[key].price_per_hour:
            best[key] = PriceQuote(
                provider=PROVIDER,
                gpu=gpu,
                price_per_hour=round(float(price), 4),
                kind="on-demand",
                source_url=source_url,
                detail=f"{row.get('gpu')} via {provider_name}",
                region=row.get("region") or "",
            )
    return list(best.values())
