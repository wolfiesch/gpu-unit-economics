import pytest
from web.providers import PriceQuote, aws


def test_aws_fetch_returns_cheapest_spot_price_per_gpu_and_skips_unusable_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "config": {
            "regions": [
                {
                    "region": "us-east-1",
                    "instanceTypes": [
                        {
                            "sizes": [
                                {
                                    "size": "p5.48xlarge",
                                    "valueColumns": [
                                        {"name": "linux", "prices": {"USD": "16.0"}}
                                    ],
                                },
                                {
                                    "size": "p5en.48xlarge",
                                    "valueColumns": [
                                        {"name": "linux", "prices": {"USD": "N/A*"}}
                                    ],
                                },
                                {
                                    "size": "c5.large",
                                    "valueColumns": [
                                        {"name": "linux", "prices": {"USD": "0.1"}}
                                    ],
                                },
                            ]
                        }
                    ],
                },
                {
                    "region": "us-west-2",
                    "instanceTypes": [
                        {
                            "sizes": [
                                {
                                    "size": "p5.4xlarge",
                                    "valueColumns": [
                                        {"name": "linux", "prices": {"USD": "1.5"}}
                                    ],
                                },
                                {
                                    "size": "p6-b200.48xlarge",
                                    "valueColumns": [
                                        {"name": "linux", "prices": {"USD": "24.0"}}
                                    ],
                                },
                            ]
                        }
                    ],
                },
            ]
        }
    }

    def fake_http_json(url: str, body: dict | None = None, headers: dict | None = None) -> dict:
        assert url == aws.API
        assert body is None
        assert headers is None
        return payload

    monkeypatch.setattr(aws, "http_json", fake_http_json)

    quotes = {quote.gpu: quote for quote in aws.fetch()}

    assert quotes == {
        "H100": PriceQuote(
            provider="aws-spot",
            gpu="H100",
            price_per_hour=1.5,
            kind="spot",
            source_url="https://aws.amazon.com/ec2/spot/pricing/",
            detail="p5.4xlarge (1x), us-west-2",
        ),
        "B200": PriceQuote(
            provider="aws-spot",
            gpu="B200",
            price_per_hour=3.0,
            kind="spot",
            source_url="https://aws.amazon.com/ec2/spot/pricing/",
            detail="p6-b200.48xlarge (8x), us-west-2",
        ),
    }
