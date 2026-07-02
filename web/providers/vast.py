"""Vast.ai marketplace prices via the public keyless bundles search endpoint."""

from __future__ import annotations

import json
import urllib.parse

from . import PriceQuote, http_json, normalize_gpu_name

PROVIDER = "vast.ai"
SOURCE_URL = "https://vast.ai/pricing"
API = "https://console.vast.ai/api/v0/bundles/"

# Marketplace listings per canonical GPU. Vast uses variant labels (SXM/PCIE/NVL).
GPU_QUERY_NAMES = ["H100 SXM", "H100 PCIE", "H100 NVL", "H200", "B200"]


def fetch() -> list[PriceQuote]:
    query = {
        "verified": {"eq": True},
        "rentable": {"eq": True},
        "num_gpus": {"eq": 1},
        "gpu_name": {"in": GPU_QUERY_NAMES},
        "order": [["dph_total", "asc"]],
        "limit": 256,
        "type": "on-demand",
    }
    url = API + "?q=" + urllib.parse.quote(json.dumps(query))
    payload = http_json(url)
    offers = payload.get("offers", [])

    # Cheapest verified single-GPU offer per (canonical name, region).
    best: dict[tuple[str, str], PriceQuote] = {}
    for offer in offers:
        gpu = normalize_gpu_name(offer.get("gpu_name", ""))
        price = offer.get("dph_total")
        if gpu is None or not price or price <= 0:
            continue
        region = offer.get("geolocation") or "unknown"
        key = (gpu, region)
        if key not in best or price < best[key].price_per_hour:
            best[key] = PriceQuote(
                provider=PROVIDER,
                gpu=gpu,
                price_per_hour=round(float(price), 4),
                kind="on-demand",
                source_url=SOURCE_URL,
                detail=offer.get("gpu_name", ""),
                region=region,
            )
    return list(best.values())
