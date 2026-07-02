"""RunPod prices via the public keyless GraphQL gpuTypes query."""

from __future__ import annotations

from . import PriceQuote, http_json, normalize_gpu_name

PROVIDER = "runpod"
SOURCE_URL = "https://www.runpod.io/pricing"
API = "https://api.runpod.io/graphql"

QUERY = """
query {
  gpuTypes {
    id
    displayName
    memoryInGb
    securePrice
    communityPrice
    lowestPrice(input: {gpuCount: 1}) {
      uninterruptablePrice
    }
  }
}
"""


def fetch() -> list[PriceQuote]:
    payload = http_json(API, body={"query": QUERY})
    gpu_types = payload["data"]["gpuTypes"]

    # Cheapest positive price per canonical GPU; prefer noting which tier it is.
    best: dict[str, PriceQuote] = {}
    for g in gpu_types:
        gpu = normalize_gpu_name(g.get("displayName", ""))
        if gpu is None:
            continue
        # A null uninterruptable lowest price means the SKU has no real
        # availability; its list prices are placeholders (e.g. H200 NVL @ $0.50).
        lowest = (g.get("lowestPrice") or {}).get("uninterruptablePrice")
        if not lowest:
            continue
        candidates = [
            ("secure", g.get("securePrice")),
            ("community", g.get("communityPrice")),
        ]
        for kind, price in candidates:
            if not price or price <= 0:
                continue
            if gpu not in best or price < best[gpu].price_per_hour:
                best[gpu] = PriceQuote(
                    provider=PROVIDER,
                    gpu=gpu,
                    price_per_hour=round(float(price), 4),
                    kind=kind,
                    source_url=SOURCE_URL,
                    detail=f"{g.get('displayName', '')} {g.get('memoryInGb', '?')}GB",
                )
    return list(best.values())
