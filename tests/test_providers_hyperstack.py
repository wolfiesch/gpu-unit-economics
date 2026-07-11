import pytest
from web.providers import PriceQuote, hyperstack


def test_hyperstack_fetch_keeps_canonical_gpu_price_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = [
        {
            "name": "NVIDIA H100 SXM",
            "price_per_hour": "2.80",
            "region": "CANADA-1",
        },
        {
            "name": "NVIDIA H200 SXM",
            "price_per_hour": 3.75,
            "region": "US-1",
        },
        {
            "name": "NVIDIA A100",
            "price_per_hour": 1.25,
            "region": "US-1",
        },
        {
            "name": "NVIDIA B200",
            "price_per_hour": None,
            "region": "EU-1",
        },
    ]

    def fake_http_json(
        url: str,
        body: dict | None = None,
        headers: dict | None = None,
    ) -> list[dict]:
        assert url == hyperstack.API
        assert body is None
        assert headers == {"api_key": "hyper-key"}
        return payload

    monkeypatch.setenv("HYPERSTACK_API_KEY", "hyper-key")
    monkeypatch.setattr(hyperstack, "http_json", fake_http_json)

    assert hyperstack.fetch() == [
        PriceQuote(
            provider="hyperstack",
            gpu="H100",
            price_per_hour=2.8,
            kind="on-demand",
            source_url="https://www.hyperstack.cloud/gpu-pricing",
            detail="NVIDIA H100 SXM",
            region="CANADA-1",
        ),
        PriceQuote(
            provider="hyperstack",
            gpu="H200",
            price_per_hour=3.75,
            kind="on-demand",
            source_url="https://www.hyperstack.cloud/gpu-pricing",
            detail="NVIDIA H200 SXM",
            region="US-1",
        ),
    ]


def test_hyperstack_fetch_returns_empty_when_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HYPERSTACK_API_KEY", raising=False)

    assert hyperstack.fetch() == []
