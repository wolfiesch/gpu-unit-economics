from pathlib import Path

from web import collect_prices
from web.providers import PriceQuote
from web.store import PriceStore


def test_run_once_records_a_scheduled_run(
    tmp_path: Path, monkeypatch
) -> None:
    store = PriceStore(tmp_path / "prices.db")
    quote = PriceQuote("runpod", "H100", 2.0, "secure", "https://example.test")
    monkeypatch.setattr("web.collector.fetch_all", lambda: ([quote], []))
    monkeypatch.setattr(
        collect_prices.Collector,
        "run",
        lambda self, trigger="scheduled": {
            "started": True,
            "run": {"status": "success", "quote_count": 0, "trigger": trigger},
        },
    )

    result = collect_prices.run_once(store)

    assert result["run"]["trigger"] == "scheduled"
    assert "alerts" not in result
