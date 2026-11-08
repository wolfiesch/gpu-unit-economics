import pytest
from web.providers import PriceQuote, lium


def _node(
    gpu_model: str,
    price: float,
    tier: str = "secure",
    available: int = 8,
    cc: str = "US",
    min_rentable: int | None = 1,
) -> dict:
    return {
        "gpu_model": gpu_model,
        "price_per_gpu_hour": price,
        "available_gpu_count": available,
        "tier": tier,
        "country_code": cc,
        "gpu_count": 8,
        **({} if min_rentable is None else {"min_rentable_gpu_count": min_rentable}),
    }


def test_lium_fetch_quotes_cheapest_per_gpu_rentable_node_per_gpu_tier_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "nodes": [
            _node("H100 80GB HBM3", 2.9),
            _node("H100 PCIe", 2.5),
            _node("H100 80GB HBM3", 1.45, tier="spot"),
            _node("H100 80GB HBM3", 1.0, available=0),
            _node("H200", 4.08, cc="DE"),
            _node("RTX 4090", 0.4),
            _node("B200", 0),
            _node("H100 80GB HBM3", 0.5, min_rentable=8),
            _node("H200", 1.0, cc="DE", min_rentable=None),
        ]
    }

    def fake_http_json(url: str, body: dict | None = None, headers: dict | None = None) -> dict:
        assert url == "https://lium.io/api/public/v1/nodes"
        assert body is None
        return payload

    monkeypatch.setattr(lium, "http_json", fake_http_json)

    assert lium.fetch() == [
        PriceQuote("lium", "H100", 2.5, "secure", "https://lium.io/pricing", "H100 PCIe", "US"),
        PriceQuote("lium", "H100", 1.45, "spot", "https://lium.io/pricing", "H100 80GB HBM3", "US"),
        PriceQuote("lium", "H200", 4.08, "secure", "https://lium.io/pricing", "H200", "DE"),
    ]


def test_lium_fetch_returns_empty_when_nodes_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lium, "http_json", lambda *args, **kwargs: {})

    assert lium.fetch() == []
