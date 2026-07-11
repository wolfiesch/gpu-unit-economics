import pytest
from web.providers import PriceQuote, lambdalabs


def test_lambdalabs_fetch_expands_regions_and_divides_instance_price_per_gpu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "data": {
            "gpu_8x_h100_sxm5": {
                "name": "8x NVIDIA H100 SXM5",
                "price_cents_per_hour": 2392,
                "specs": {"gpus": 8},
                "regions": ["us-east-1", "us-west-1"],
            },
            "gpu_1x_a10": {
                "name": "1x NVIDIA A10",
                "price_cents_per_hour": 75,
                "specs": {"gpus": 1},
                "regions": ["us-east-1"],
            },
            "gpu_4x_b200": {
                "name": "4x NVIDIA B200",
                "price_cents_per_hour": 1600,
                "specs": {"gpus": 4},
                "regions": [],
            },
        }
    }

    def fake_http_json(
        url: str,
        body: dict | None = None,
        headers: dict | None = None,
    ) -> dict:
        assert url == lambdalabs.API
        assert body is None
        assert headers == {"Authorization": "Bearer lambda-key"}
        return payload

    monkeypatch.setenv("LAMBDA_API_KEY", "lambda-key")
    monkeypatch.setattr(lambdalabs, "http_json", fake_http_json)

    assert lambdalabs.fetch() == [
        PriceQuote(
            provider="lambda",
            gpu="H100",
            price_per_hour=2.99,
            kind="on-demand",
            source_url="https://lambda.ai/pricing",
            detail="8x NVIDIA H100 SXM5 (8x)",
            region="us-east-1",
        ),
        PriceQuote(
            provider="lambda",
            gpu="H100",
            price_per_hour=2.99,
            kind="on-demand",
            source_url="https://lambda.ai/pricing",
            detail="8x NVIDIA H100 SXM5 (8x)",
            region="us-west-1",
        ),
    ]


def test_lambdalabs_fetch_returns_empty_when_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAMBDA_API_KEY", raising=False)

    assert lambdalabs.fetch() == []
