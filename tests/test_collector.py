from pathlib import Path

from web.collector import Collector, collect_prices
from web.providers import PriceQuote
from web.store import PriceStore


def quote(provider: str = "runpod") -> PriceQuote:
    return PriceQuote(provider, "H100", 2.0, "secure", "https://example.test")


def test_collector_classifies_success_partial_and_failed(tmp_path: Path) -> None:
    store = PriceStore(tmp_path / "prices.db")

    success = collect_prices(store, fetcher=lambda: ([quote()], []))
    partial = collect_prices(
        store, fetcher=lambda: ([quote()], ["aws-spot: timeout"])
    )
    failed = collect_prices(store, fetcher=lambda: ([], ["runpod: offline"]))

    assert success["run"]["status"] == "success"
    assert partial["run"]["status"] == "partial"
    assert failed["run"]["status"] == "failed"
    failed_provider = next(
        item for item in failed["run"]["providers"] if item["provider"] == "runpod"
    )
    assert failed_provider["status"] == "failed"


def test_collector_does_not_overlap_an_active_run(tmp_path: Path) -> None:
    store = PriceStore(tmp_path / "prices.db")
    assert store.begin_collection_run() is not None
    called = False

    def fetcher():
        nonlocal called
        called = True
        return [quote()], []

    assert collect_prices(store, fetcher=fetcher) == {
        "started": False,
        "reason": "collection_already_running",
    }
    assert called is False


def test_collector_closes_run_when_fetcher_crashes(tmp_path: Path) -> None:
    store = PriceStore(tmp_path / "prices.db")

    def fetcher():
        raise RuntimeError("collector crashed")

    result = collect_prices(store, fetcher=fetcher)

    assert result["run"]["status"] == "failed"
    assert result["run"]["error"] == "collector crashed"
    assert store.collection_health()["running"] is None


def test_collector_class_exposes_run_entry_point(tmp_path: Path) -> None:
    store = PriceStore(tmp_path / "prices.db")
    result = Collector(store, fetcher=lambda: ([quote()], [])).run(trigger="manual")

    assert result["run"]["trigger"] == "manual"
    assert result["run"]["status"] == "success"
