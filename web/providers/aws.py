"""AWS EC2 spot prices via the public keyless spot.json feed.

This is the same feed the EC2 spot pricing page uses. Instance prices are for
whole nodes; we divide by the GPU count to get a per-GPU rate. Cheapest region
wins per canonical GPU.
"""

from __future__ import annotations

import threading

from . import PriceQuote, http_json_conditional

PROVIDER = "aws-spot"
SOURCE_URL = "https://aws.amazon.com/ec2/spot/pricing/"
API = "https://website.spot.ec2.aws.a2z.com/spot.json"

# instance size -> (canonical GPU, GPUs per instance)
INSTANCE_GPUS = {
    "p5.4xlarge": ("H100", 1),
    "p5.48xlarge": ("H100", 8),
    "p5e.48xlarge": ("H200", 8),
    "p5en.48xlarge": ("H200", 8),
    "p6-b200.48xlarge": ("B200", 8),
}


# spot.json is a ~2.6MB CDN object regenerated every few minutes. Cache the
# parsed quotes keyed by ETag so a 304 skips both download and re-parse.
_cache_lock = threading.Lock()
_cached_etag: str | None = None
_cached_quotes: list[PriceQuote] | None = None


def fetch() -> list[PriceQuote]:
    global _cached_etag, _cached_quotes
    with _cache_lock:
        etag = _cached_etag
    payload, new_etag = http_json_conditional(API, etag)
    if payload is None:
        with _cache_lock:
            if _cached_quotes is not None:
                return list(_cached_quotes)
        # 304 without a warm cache (e.g. process restarted mid-flight): refetch.
        payload, new_etag = http_json_conditional(API, None)
    quotes = _parse(payload)
    with _cache_lock:
        _cached_etag = new_etag
        _cached_quotes = list(quotes)
    return quotes


def _parse(payload: dict) -> list[PriceQuote]:
    best: dict[tuple[str, str], PriceQuote] = {}
    for region in payload["config"]["regions"]:
        region_name = region.get("region", "unknown")
        for itype in region.get("instanceTypes", []):
            for size in itype.get("sizes", []):
                mapping = INSTANCE_GPUS.get(size.get("size", ""))
                if mapping is None:
                    continue
                gpu, n_gpus = mapping
                linux = next(
                    (v["prices"]["USD"] for v in size.get("valueColumns", [])
                     if v.get("name") == "linux"),
                    None,
                )
                try:
                    per_gpu = float(linux) / n_gpus
                except (TypeError, ValueError):
                    continue  # "N/A*" placeholders for unavailable regions
                if per_gpu <= 0:
                    continue
                key = (gpu, region_name)
                if key not in best or per_gpu < best[key].price_per_hour:
                    best[key] = PriceQuote(
                        provider=PROVIDER,
                        gpu=gpu,
                        price_per_hour=round(per_gpu, 4),
                        kind="spot",
                        source_url=SOURCE_URL,
                        detail=f"{size['size']} ({n_gpus}x)",
                        region=region_name,
                    )
    return list(best.values())
