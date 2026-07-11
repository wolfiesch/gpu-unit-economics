"""Reusable scheduled price collection with durable run lineage."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any

from .providers import PriceQuote, fetch_all
from .store import PriceStore

PROVIDERS = (
    "vast.ai",
    "runpod",
    "aws-spot",
    "azure",
    "computeprices",
    "lambda",
    "hyperstack",
    "sfcompute",
)

FetchPrices = Callable[[], tuple[list[PriceQuote], list[str]]]


def _provider_results(
    quotes: list[PriceQuote], errors: list[str]
) -> list[dict[str, Any]]:
    counts = Counter(quote.provider for quote in quotes)
    failures: dict[str, str] = {}
    for error in errors:
        provider, separator, _ = error.partition(":")
        if separator:
            failures[provider.strip()] = error

    provider_names = sorted(set(PROVIDERS) | set(counts) | set(failures))
    return [
        {
            "provider": provider,
            "status": "failed" if provider in failures else "success",
            "quote_count": counts[provider],
            "error": failures.get(provider, ""),
        }
        for provider in provider_names
    ]


def collect_prices(
    store: PriceStore,
    *,
    trigger: str = "scheduled",
    fetcher: FetchPrices = fetch_all,
) -> dict[str, Any]:
    """Collect one batch, or report that another process owns the run slot."""
    run_id = store.begin_collection_run(trigger)
    if run_id is None:
        return {"started": False, "reason": "collection_already_running"}

    try:
        quotes, errors = fetcher()
        run = store.finish_collection_run(
            run_id, quotes, errors, _provider_results(quotes, errors)
        )
    except Exception as exc:
        run = store.fail_collection_run(run_id, str(exc))
    return {"started": True, "run": run}


class Collector:
    """Configured collection service for schedulers, commands, and tests."""

    def __init__(self, store: PriceStore, fetcher: FetchPrices = fetch_all) -> None:
        self.store = store
        self.fetcher = fetcher

    def run(self, trigger: str = "scheduled") -> dict[str, Any]:
        return collect_prices(self.store, trigger=trigger, fetcher=self.fetcher)
