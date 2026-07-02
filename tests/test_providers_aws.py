import json
import urllib.error

import pytest
import web.providers as providers
from web.providers import PriceQuote, aws


@pytest.fixture(autouse=True)
def reset_aws_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(aws, "_cached_etag", None)
    monkeypatch.setattr(aws, "_cached_quotes", None)

def test_aws_fetch_returns_cheapest_spot_price_per_gpu_and_region_and_skips_unusable_rows(
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

    def fake_http_json_conditional(
        url: str, etag: str | None
    ) -> tuple[dict | None, str | None]:
        assert url == aws.API
        assert etag is None
        return payload, "spot-etag"

    monkeypatch.setattr(aws, "http_json_conditional", fake_http_json_conditional)

    quotes = {(quote.gpu, quote.region): quote for quote in aws.fetch()}

    assert quotes == {
        ("H100", "us-east-1"): PriceQuote(
            provider="aws-spot",
            gpu="H100",
            price_per_hour=2.0,
            kind="spot",
            source_url="https://aws.amazon.com/ec2/spot/pricing/",
            detail="p5.48xlarge (8x)",
            region="us-east-1",
        ),
        ("H100", "us-west-2"): PriceQuote(
            provider="aws-spot",
            gpu="H100",
            price_per_hour=1.5,
            kind="spot",
            source_url="https://aws.amazon.com/ec2/spot/pricing/",
            detail="p5.4xlarge (1x)",
            region="us-west-2",
        ),
        ("B200", "us-west-2"): PriceQuote(
            provider="aws-spot",
            gpu="B200",
            price_per_hour=3.0,
            kind="spot",
            source_url="https://aws.amazon.com/ec2/spot/pricing/",
            detail="p6-b200.48xlarge (8x)",
            region="us-west-2",
        ),
    }


def test_http_json_conditional_sends_if_none_match_and_returns_payload_etag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = []

    class Response:
        headers = {"ETag": '"next-etag"'}

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"ok": True}).encode()

    def fake_urlopen(request, timeout: int) -> Response:
        requests.append(request)
        assert request.full_url == "https://example.test/feed.json"
        assert timeout == providers.FETCH_TIMEOUT_S
        return Response()

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)

    assert providers.http_json_conditional(
        "https://example.test/feed.json", '"old-etag"'
    ) == ({"ok": True}, '"next-etag"')
    assert len(requests) == 1
    assert requests[0].get_header("If-none-match") == '"old-etag"'


def test_http_json_conditional_maps_304_to_cached_payload_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request, timeout: int):
        raise urllib.error.HTTPError(
            request.full_url,
            304,
            "Not Modified",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)

    assert providers.http_json_conditional(
        "https://example.test/feed.json", '"old-etag"'
    ) == (None, '"old-etag"')


def test_http_json_conditional_reraises_non_304_http_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request, timeout: int):
        raise urllib.error.HTTPError(
            request.full_url,
            500,
            "Internal Server Error",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        providers.http_json_conditional("https://example.test/feed.json", '"old-etag"')

    assert exc_info.value.code == 500


def test_aws_fetch_reuses_cached_quotes_on_not_modified_without_reparsing(
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
                                    "size": "p5.4xlarge",
                                    "valueColumns": [
                                        {"name": "linux", "prices": {"USD": "1.25"}}
                                    ],
                                }
                            ]
                        }
                    ],
                }
            ]
        }
    }
    expected_quote = PriceQuote(
        provider="aws-spot",
        gpu="H100",
        price_per_hour=1.25,
        kind="spot",
        source_url="https://aws.amazon.com/ec2/spot/pricing/",
        detail="p5.4xlarge (1x)",
        region="us-east-1",
    )
    calls: list[tuple[str, str | None]] = []
    parse_calls = 0
    real_parse = aws._parse

    def fake_http_json_conditional(
        url: str, etag: str | None
    ) -> tuple[dict | None, str | None]:
        calls.append((url, etag))
        if len(calls) == 1:
            return payload, '"etag-1"'
        return None, '"etag-1"'

    def counting_parse(payload_arg: dict) -> list[PriceQuote]:
        nonlocal parse_calls
        parse_calls += 1
        return real_parse(payload_arg)

    monkeypatch.setattr(aws, "http_json_conditional", fake_http_json_conditional)
    monkeypatch.setattr(aws, "_parse", counting_parse)

    first = aws.fetch()
    first.append(
        PriceQuote(
            provider="sentinel",
            gpu="H100",
            price_per_hour=99.0,
            kind="test",
            source_url="https://example.test/sentinel",
        )
    )
    second = aws.fetch()

    assert second == [expected_quote]
    assert calls == [(aws.API, None), (aws.API, '"etag-1"')]
    assert parse_calls == 1


def test_aws_fetch_refetches_without_etag_when_not_modified_has_no_warm_quotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "config": {
            "regions": [
                {
                    "region": "us-west-2",
                    "instanceTypes": [
                        {
                            "sizes": [
                                {
                                    "size": "p6-b200.48xlarge",
                                    "valueColumns": [
                                        {"name": "linux", "prices": {"USD": "24.0"}}
                                    ],
                                }
                            ]
                        }
                    ],
                }
            ]
        }
    }
    calls: list[tuple[str, str | None]] = []

    def fake_http_json_conditional(
        url: str, etag: str | None
    ) -> tuple[dict | None, str | None]:
        calls.append((url, etag))
        if len(calls) == 1:
            return None, '"stale-etag"'
        return payload, '"fresh-etag"'

    monkeypatch.setattr(aws, "_cached_etag", '"stale-etag"')
    monkeypatch.setattr(aws, "http_json_conditional", fake_http_json_conditional)

    assert aws.fetch() == [
        PriceQuote(
            provider="aws-spot",
            gpu="B200",
            price_per_hour=3.0,
            kind="spot",
            source_url="https://aws.amazon.com/ec2/spot/pricing/",
            detail="p6-b200.48xlarge (8x)",
            region="us-west-2",
        )
    ]
    assert calls == [(aws.API, '"stale-etag"'), (aws.API, None)]
