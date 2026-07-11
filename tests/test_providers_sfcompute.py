import pytest
from web.providers import PriceQuote, sfcompute


def test_sfcompute_fetch_quotes_best_ask_per_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"best_ask": {"dollars_per_node_hour": "32.00"}}

    def fake_http_json(
        url: str,
        body: dict | None = None,
        headers: dict | None = None,
    ) -> dict:
        assert url == (
            "https://api.sfcompute.com/preview/v2/orderbook/quote?"
            "requirements=accelerator:H100&start_at=4600&end_at=8200"
        )
        assert body is None
        assert headers == {"Authorization": "Bearer sf-key"}
        return payload

    monkeypatch.setenv("SFCOMPUTE_API_KEY", "sf-key")
    monkeypatch.setattr(sfcompute.time, "time", lambda: 1000.2)
    monkeypatch.setattr(sfcompute, "http_json", fake_http_json)

    assert sfcompute.fetch() == [
        PriceQuote(
            provider="sfcompute",
            gpu="H100",
            price_per_hour=4.0,
            kind="spot",
            source_url="https://sfcompute.com/prices",
            detail="orderbook best ask, per-node/8",
        )
    ]


def test_sfcompute_fetch_returns_empty_when_best_ask_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SFCOMPUTE_API_KEY", "sf-key")
    monkeypatch.setattr(sfcompute.time, "time", lambda: 1000.0)
    monkeypatch.setattr(sfcompute, "http_json", lambda *args, **kwargs: {})

    assert sfcompute.fetch() == []


def test_sfcompute_fetch_returns_empty_when_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SFCOMPUTE_API_KEY", raising=False)

    assert sfcompute.fetch() == []
