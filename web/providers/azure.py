"""Azure VM retail prices via the public keyless Retail Prices API."""

from __future__ import annotations

import urllib.parse
from collections.abc import Iterable

from . import PriceQuote, http_json

PROVIDER = "azure"
SOURCE_URL = "https://azure.microsoft.com/en-us/pricing/details/virtual-machines/"
API = "https://prices.azure.com/api/retail/prices"

_FILTER = (
    "serviceName eq 'Virtual Machines' and "
    "priceType eq 'Consumption' and "
    "contains(armSkuName, 'ND96')"
)
_MAX_PAGES = 8


def classify(armSkuName: str) -> tuple[str, int] | None:
    """Return the canonical GPU and count for Azure ND96 GPU SKUs."""
    sku = armSkuName.upper()
    if "ND96" not in sku:
        return None
    if "H100" in sku:
        return "H100", 8
    if "H200" in sku:
        return "H200", 8
    return None


def fetch() -> list[PriceQuote]:
    url = API + "?$filter=" + urllib.parse.quote(_FILTER) + "&$top=1000"
    best: dict[tuple[str, str], PriceQuote] = {}

    for _ in range(_MAX_PAGES):
        payload = http_json(url)
        _parse_items(payload.get("Items", ()), best)
        next_page = payload.get("NextPageLink")
        if not next_page:
            break
        url = next_page

    return list(best.values())


def _parse_items(
    items: Iterable[dict],
    best: dict[tuple[str, str], PriceQuote],
) -> None:
    for item in items:
        if item.get("unitOfMeasure") != "1 Hour":
            continue
        meter_name = item.get("meterName", "")
        if "Spot" in meter_name or "Low Priority" in meter_name:
            continue
        try:
            retail_price = float(item.get("retailPrice", 0))
        except (TypeError, ValueError):
            continue
        if retail_price <= 0:
            continue

        arm_sku_name = item.get("armSkuName", "")
        mapping = classify(arm_sku_name)
        if mapping is None:
            continue
        gpu, gpu_count = mapping
        region = item.get("armRegionName") or ""
        per_gpu = retail_price / gpu_count
        key = (gpu, region)
        if key not in best or per_gpu < best[key].price_per_hour:
            best[key] = PriceQuote(
                provider=PROVIDER,
                gpu=gpu,
                price_per_hour=round(per_gpu, 4),
                kind="on-demand",
                source_url=SOURCE_URL,
                detail=f"{arm_sku_name} ({gpu_count}x)",
                region=region,
            )
