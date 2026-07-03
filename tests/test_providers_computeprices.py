import pytest
from web.providers import PriceQuote, computeprices


def test_computeprices_fetch_parses_data_payload_filters_rows_and_keeps_cheapest_provider_quote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "data": [
            {
                "provider": "Lambda",
                "gpu": "H100",
                "gpu_slug": "h100",
                "price_per_hour_usd": "3.20",
                "pricing_type": "on_demand",
                "region": "us-east-1",
                "source_url": "https://lambda.example/h100",
            },
            {
                "provider": "Lambda",
                "gpu": "H100",
                "gpu_slug": "h100",
                "price_per_hour_usd": "2.40",
                "pricing_type": "on_demand",
                "source_url": "http://lambda.example/not-secure",
            },
            {
                "provider": "Modal",
                "gpu": "H100",
                "gpu_slug": "h100",
                "price_per_hour_usd": "0.066",
                "pricing_type": "on_demand",
                "region": "us-east-1",
                "source_url": "https://modal.example/h100",
            },
            {
                "provider": "ReservedCloud",
                "gpu": "H100",
                "gpu_slug": "h100",
                "price_per_hour_usd": "1.00",
                "pricing_type": "reserved",
                "region": "us-east-1",
                "source_url": "https://reserved.example/h100",
            },
            {
                "provider": "SpotCloud",
                "gpu": "H200",
                "gpu_slug": "h200",
                "price_per_hour_usd": "1.20",
                "pricing_type": "spot",
                "region": "us-west-2",
                "source_url": "https://spot.example/h200",
            },
            {
                "provider": "Azure",
                "gpu": "H200",
                "gpu_slug": "h200",
                "price_per_hour_usd": "5.50",
                "pricing_type": "on_demand",
                "region": "eastus",
                "source_url": "https://azure.example/h200",
            },
            {
                "provider": "Nebula",
                "gpu": "B200",
                "gpu_slug": "b200",
                "price_per_hour_usd": "0.99",
                "pricing_type": "on_demand",
                "region": "us-central",
                "source_url": "https://nebula.example/b200-too-cheap",
            },
            {
                "provider": "Nebula",
                "gpu": "B200",
                "gpu_slug": "b200",
                "price_per_hour_usd": "1.25",
                "pricing_type": "on_demand",
                "region": "us-central",
                "source_url": "https://nebula.example/b200",
            },
            {
                "provider": "Legacy",
                "gpu": "L40S",
                "gpu_slug": "l40s",
                "price_per_hour_usd": "0.80",
                "pricing_type": "on_demand",
                "region": "us-east-1",
                "source_url": "https://legacy.example/l40s",
            },
        ]
    }

    def fake_http_json(url: str) -> dict:
        assert url == computeprices.API
        return payload

    monkeypatch.setattr(computeprices, "http_json", fake_http_json)

    quotes = {(quote.gpu, quote.detail): quote for quote in computeprices.fetch()}

    assert quotes == {
        ("H100", "H100 via Lambda"): PriceQuote(
            provider="computeprices",
            gpu="H100",
            price_per_hour=2.4,
            kind="on-demand",
            source_url="https://computeprices.com",
            detail="H100 via Lambda",
            region="",
        ),
        ("H200", "H200 via Azure"): PriceQuote(
            provider="computeprices",
            gpu="H200",
            price_per_hour=5.5,
            kind="on-demand",
            source_url="https://azure.example/h200",
            detail="H200 via Azure",
            region="eastus",
        ),
        ("B200", "B200 via Nebula"): PriceQuote(
            provider="computeprices",
            gpu="B200",
            price_per_hour=1.25,
            kind="on-demand",
            source_url="https://nebula.example/b200",
            detail="B200 via Nebula",
            region="us-central",
        ),
    }


def test_computeprices_fetch_accepts_bare_list_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = [
        {
            "provider": "Vast",
            "gpu": "H100 SXM",
            "gpu_slug": "h100",
            "price_per_hour_usd": 1.75,
            "pricing_type": "on_demand",
            "region": "us-west",
            "source_url": "https://vast.example/h100",
        }
    ]

    def fake_http_json(url: str) -> list[dict]:
        assert url == computeprices.API
        return payload

    monkeypatch.setattr(computeprices, "http_json", fake_http_json)

    assert computeprices.fetch() == [
        PriceQuote(
            provider="computeprices",
            gpu="H100",
            price_per_hour=1.75,
            kind="on-demand",
            source_url="https://vast.example/h100",
            detail="H100 SXM via Vast",
            region="us-west",
        )
    ]
