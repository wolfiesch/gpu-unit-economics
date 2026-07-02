"""AWS EC2 spot prices via the public keyless spot.json feed.

This is the same feed the EC2 spot pricing page uses. Instance prices are for
whole nodes; we divide by the GPU count to get a per-GPU rate. Cheapest region
wins per canonical GPU.
"""

from __future__ import annotations

from . import PriceQuote, http_json

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


def fetch() -> list[PriceQuote]:
    payload = http_json(API)
    best: dict[str, PriceQuote] = {}
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
                if gpu not in best or per_gpu < best[gpu].price_per_hour:
                    best[gpu] = PriceQuote(
                        provider=PROVIDER,
                        gpu=gpu,
                        price_per_hour=round(per_gpu, 4),
                        kind="spot",
                        source_url=SOURCE_URL,
                        detail=f"{size['size']} ({n_gpus}x), {region_name}",
                    )
    return list(best.values())
