"""Lambda Cloud instance-type prices via the authenticated Cloud API."""

from __future__ import annotations

import os
from collections.abc import Iterable

from . import PriceQuote, http_json, normalize_gpu_name

PROVIDER = "lambda"
SOURCE_URL = "https://lambda.ai/pricing"
API = "https://cloud.lambdalabs.com/api/v1/instance-types"
ENV_KEY = "LAMBDA_API_KEY"


def fetch() -> list[PriceQuote]:
    key = os.environ.get(ENV_KEY)
    if not key:
        return []

    payload = http_json(API, headers={"Authorization": f"Bearer {key}"})
    rows = payload.get("data", ()) if isinstance(payload, dict) else ()
    return _parse_rows(_iter_instance_types(rows))


def _iter_instance_types(rows: object) -> Iterable[dict]:
    if isinstance(rows, dict):
        for instance_type, row in rows.items():
            if isinstance(row, dict):
                if "name" in row:
                    yield row
                else:
                    yield {"name": instance_type, **row}
    elif isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                yield row


def _parse_rows(rows: Iterable[dict]) -> list[PriceQuote]:
    quotes: list[PriceQuote] = []
    for row in rows:
        name = str(row.get("name") or "")
        gpu = normalize_gpu_name(name)
        if gpu is None:
            continue

        try:
            cents_per_hour = float(row.get("price_cents_per_hour"))
            gpu_count = int(row.get("specs", {}).get("gpus"))
        except (TypeError, ValueError, AttributeError):
            continue
        if cents_per_hour <= 0 or gpu_count <= 0:
            continue

        price_per_gpu = round(cents_per_hour / 100 / gpu_count, 4)
        for region in row.get("regions") or ():
            if isinstance(region, str):
                quotes.append(
                    PriceQuote(
                        provider=PROVIDER,
                        gpu=gpu,
                        price_per_hour=price_per_gpu,
                        kind="on-demand",
                        source_url=SOURCE_URL,
                        detail=f"{name} ({gpu_count}x)",
                        region=region,
                    )
                )
    return quotes
