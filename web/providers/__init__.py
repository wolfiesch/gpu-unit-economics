"""Live market-price providers for GPU cloud rentals.

Each provider module exposes `fetch() -> list[PriceQuote]`. Providers hit public,
keyless endpoints and normalize results to canonical GPU names matching
`gpu_econ.inputs.DEFAULT_GPUS` (H100, H200, B200). All network access lives here,
behind the backend — the browser never talks to vendors directly.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import asdict, dataclass

FETCH_TIMEOUT_S = 15
USER_AGENT = "gpu-unit-economics/1.0 (+https://github.com/wolfiesch/gpu-unit-economics)"

# Canonical GPU keys the model knows about.
CANONICAL_GPUS = ("H100", "H200", "B200")


@dataclass(frozen=True)
class PriceQuote:
    """One provider's current price for one canonical GPU."""

    provider: str
    gpu: str  # canonical name: H100 | H200 | B200
    price_per_hour: float  # USD, on-demand / uninterruptible, single GPU
    kind: str  # e.g. "on-demand", "secure", "community"
    source_url: str  # human-readable provenance
    detail: str = ""  # provider-specific note (region, SKU variant, ...)

    def to_dict(self) -> dict:
        return asdict(self)


def http_json(url: str, body: dict | None = None, headers: dict | None = None) -> dict:
    """POST (if body) or GET a JSON endpoint. Raises on HTTP/network errors."""
    data = json.dumps(body).encode() if body is not None else None
    all_headers = {"User-Agent": USER_AGENT}
    if body is not None:
        all_headers["Content-Type"] = "application/json"
    if headers:
        all_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=all_headers)
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:
        return json.loads(resp.read())


def normalize_gpu_name(raw: str) -> str | None:
    """Map a provider's GPU label to a canonical name, or None if not tracked."""
    upper = raw.upper()
    for canonical in CANONICAL_GPUS:
        if canonical in upper:
            return canonical
    return None


def fetch_all() -> tuple[list[PriceQuote], list[str]]:
    """Fetch from every registered provider. Returns (quotes, errors).

    A provider failure never takes down the others — its error string is
    returned so the API can surface partial freshness honestly.
    """
    from . import runpod, vast

    quotes: list[PriceQuote] = []
    errors: list[str] = []
    for mod in (vast, runpod):
        try:
            quotes.extend(mod.fetch())
        except Exception as exc:  # noqa: BLE001 - upstreams are flaky by nature
            errors.append(f"{mod.PROVIDER}: {exc}")
    return quotes, errors
