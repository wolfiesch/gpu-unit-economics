"""Lium marketplace prices via the public keyless nodes endpoint."""

from __future__ import annotations

from . import PriceQuote, http_json, normalize_gpu_name

PROVIDER = "lium"
SOURCE_URL = "https://lium.io/pricing"
API = "https://lium.io/api/public/v1/nodes"


def fetch() -> list[PriceQuote]:
    payload = http_json(API)
    nodes = payload.get("nodes", []) if isinstance(payload, dict) else []

    # Cheapest available node per (canonical name, tier, country). Prices are per GPU-hour.
    best: dict[tuple[str, str, str], PriceQuote] = {}
    for node in nodes:
        if not isinstance(node, dict) or not node.get("available_gpu_count"):
            continue
        gpu = normalize_gpu_name(node.get("gpu_model") or "")
        price = node.get("price_per_gpu_hour")
        if gpu is None or not isinstance(price, (int, float)) or price <= 0:
            continue
        kind = node.get("tier") or "on-demand"
        region = node.get("country_code") or "unknown"
        key = (gpu, kind, region)
        if key not in best or price < best[key].price_per_hour:
            best[key] = PriceQuote(
                provider=PROVIDER,
                gpu=gpu,
                price_per_hour=round(float(price), 4),
                kind=kind,
                source_url=SOURCE_URL,
                detail=node.get("gpu_model", ""),
                region=region,
            )
    return list(best.values())
