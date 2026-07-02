import pytest
import web.providers as providers
from web.providers import PriceQuote, fetch_all, normalize_gpu_name, runpod, vast


def test_normalize_gpu_name_maps_tracked_market_labels() -> None:
    assert normalize_gpu_name("H100 SXM") == "H100"
    assert normalize_gpu_name("NVIDIA H200 NVL") == "H200"
    assert normalize_gpu_name("B200") == "B200"
    assert normalize_gpu_name("RTX 4090") is None


def test_vast_fetch_returns_cheapest_positive_offer_per_canonical_gpu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "offers": [
            {"gpu_name": "NVIDIA H100 SXM", "dph_total": 3.2, "geolocation": "US-CA"},
            {"gpu_name": "H100 PCIE", "dph_total": 2.75, "geolocation": "EU-DE"},
            {"gpu_name": "H100 SXM", "dph_total": 0, "geolocation": "US-NY"},
            {"gpu_name": "NVIDIA H200 NVL", "dph_total": 4.4, "geolocation": "US-TX"},
            {"gpu_name": "B200", "dph_total": None, "geolocation": "US-WA"},
            {"gpu_name": "NVIDIA RTX 4090", "dph_total": 1.0, "geolocation": "US-CA"},
            {"gpu_name": "B200", "dph_total": 6.25, "geolocation": "US-NY"},
            {"gpu_name": "B200", "dph_total": 6.5, "geolocation": "US-FL"},
        ]
    }

    def fake_http_json(url: str, body: dict | None = None, headers: dict | None = None) -> dict:
        assert body is None
        assert headers is None
        assert url.startswith(vast.API)
        return payload

    monkeypatch.setattr(providers, "http_json", fake_http_json)
    monkeypatch.setattr(vast, "http_json", fake_http_json)

    quotes = {quote.gpu: quote for quote in vast.fetch()}

    assert quotes == {
        "H100": PriceQuote(
            provider="vast.ai",
            gpu="H100",
            price_per_hour=2.75,
            kind="on-demand",
            source_url="https://vast.ai/pricing",
            detail="H100 PCIE, EU-DE",
        ),
        "H200": PriceQuote(
            provider="vast.ai",
            gpu="H200",
            price_per_hour=4.4,
            kind="on-demand",
            source_url="https://vast.ai/pricing",
            detail="NVIDIA H200 NVL, US-TX",
        ),
        "B200": PriceQuote(
            provider="vast.ai",
            gpu="B200",
            price_per_hour=6.25,
            kind="on-demand",
            source_url="https://vast.ai/pricing",
            detail="B200, US-NY",
        ),
    }


def test_runpod_fetch_skips_placeholder_skus_and_labels_cheapest_positive_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "data": {
            "gpuTypes": [
                {
                    "displayName": "NVIDIA H200 NVL",
                    "memoryInGb": 141,
                    "securePrice": 1.0,
                    "communityPrice": 0.5,
                    "lowestPrice": {"uninterruptablePrice": None},
                },
                {
                    "displayName": "NVIDIA H100",
                    "memoryInGb": 80,
                    "securePrice": 2.8,
                    "communityPrice": 1.9,
                    "lowestPrice": {"uninterruptablePrice": 1.9},
                },
                {
                    "displayName": "H100 SXM",
                    "memoryInGb": 80,
                    "securePrice": 1.7,
                    "communityPrice": 2.2,
                    "lowestPrice": {"uninterruptablePrice": 1.7},
                },
                {
                    "displayName": "B200",
                    "memoryInGb": 180,
                    "securePrice": None,
                    "communityPrice": 3.4,
                    "lowestPrice": {"uninterruptablePrice": 3.4},
                },
            ]
        }
    }

    def fake_http_json(url: str, body: dict | None = None, headers: dict | None = None) -> dict:
        assert url == runpod.API
        assert body == {"query": runpod.QUERY}
        assert headers is None
        return payload

    monkeypatch.setattr(runpod, "http_json", fake_http_json)

    quotes = {quote.gpu: quote for quote in runpod.fetch()}

    assert quotes == {
        "H100": PriceQuote(
            provider="runpod",
            gpu="H100",
            price_per_hour=1.7,
            kind="secure",
            source_url="https://www.runpod.io/pricing",
            detail="H100 SXM 80GB",
        ),
        "B200": PriceQuote(
            provider="runpod",
            gpu="B200",
            price_per_hour=3.4,
            kind="community",
            source_url="https://www.runpod.io/pricing",
            detail="B200 180GB",
        ),
    }


def test_fetch_all_preserves_successful_provider_quotes_when_another_provider_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runpod_quote = PriceQuote(
        provider="runpod",
        gpu="H100",
        price_per_hour=1.7,
        kind="secure",
        source_url="https://www.runpod.io/pricing",
        detail="H100 SXM 80GB",
    )

    def fail_vast() -> list[PriceQuote]:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(vast, "fetch", fail_vast)
    monkeypatch.setattr(runpod, "fetch", lambda: [runpod_quote])

    quotes, errors = fetch_all()

    assert quotes == [runpod_quote]
    assert errors == ["vast.ai: provider unavailable"]
