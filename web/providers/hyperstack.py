"""Hyperstack GPU prices via the authenticated Infrahub pricebook API."""

from __future__ import annotations

import os
from collections.abc import Iterable

from . import PriceQuote, http_json, normalize_gpu_name

PROVIDER = "hyperstack"
SOURCE_URL = "https://www.hyperstack.cloud/gpu-pricing"
API = "https://infrahub-api.nexgencloud.com/v1/pricebook"
ENV_KEY = "HYPERSTACK_API_KEY"


def fetch() -> list[PriceQuote]:
    key = os.environ.get(ENV_KEY)
    if not key:
        return []

    payload = http_json(API, headers={"api_key": key})
    return _parse_rows(_iter_price_entries(payload))


def _iter_price_entries(payload: object) -> Iterable[dict]:
    if isinstance(payload, list):
        for row in payload:
            if isinstance(row, dict):
                yield row
    elif isinstance(payload, dict):
        rows = payload.get("data") or payload.get("prices") or payload.get("resources") or ()
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    yield row


def _parse_rows(rows: Iterable[dict]) -> list[PriceQuote]:
    quotes: list[PriceQuote] = []
    for row in rows:
        name = str(row.get("name") or row.get("gpu") or row.get("model") or "")
        gpu = normalize_gpu_name(name)
        if gpu is None:
            continue

        try:
            price = float(row.get("price_per_hour"))
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue

        quotes.append(
            PriceQuote(
                provider=PROVIDER,
                gpu=gpu,
                price_per_hour=round(price, 4),
                kind="on-demand",
                source_url=SOURCE_URL,
                detail=name,
                region=row.get("region") or "",
            )
        )
    return quotes
