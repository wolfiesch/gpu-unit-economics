import pytest
from web import token_prices


def reset_token_price_cache() -> None:
    token_prices._cache["data"] = None
    token_prices._cache["fetched_at"] = 0.0


def openrouter_payload(*models: dict) -> dict:
    return {"data": list(models)}


def test_fetch_token_prices_filters_to_valid_open_weights_and_converts_per_million(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_token_price_cache()
    payload = openrouter_payload(
        {
            "id": "meta-llama/llama-3.1-8b-instruct",
            "name": "Llama 3.1 8B Instruct",
            "pricing": {"completion": "0.00000012", "prompt": "0.00000006"},
        },
        {
            "id": "anthropic/claude-3.5-sonnet",
            "name": "Claude 3.5 Sonnet",
            "pricing": {"completion": "0.000003", "prompt": "0.0000015"},
        },
        {
            "id": "mistralai/mistral-7b",
            "name": "Mistral 7B",
            "pricing": {"prompt": "0.00000004"},
        },
    )

    def fake_http_json(url: str) -> dict:
        assert url == token_prices.API
        return payload

    monkeypatch.setattr(token_prices, "http_json", fake_http_json)

    data = token_prices.fetch_token_prices()

    assert [model["id"] for model in data["models"]] == [
        "meta-llama/llama-3.1-8b-instruct"
    ]
    llama = data["models"][0]
    assert llama["usd_per_million_output"] == pytest.approx(0.12)
    assert llama["usd_per_million_input"] == pytest.approx(0.06)


def test_fetch_token_prices_sorts_by_output_price_ascending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_token_price_cache()
    payload = openrouter_payload(
        {
            "id": "meta-llama/llama-3.1-8b-instruct",
            "name": "Llama 3.1 8B Instruct",
            "pricing": {"completion": "0.00000012", "prompt": "0.00000006"},
        },
        {
            "id": "qwen/qwen-2.5-7b-instruct",
            "name": "Qwen 2.5 7B Instruct",
            "pricing": {"completion": "0.00000005", "prompt": "0.00000002"},
        },
    )

    def fake_http_json(url: str) -> dict:
        assert url == token_prices.API
        return payload

    monkeypatch.setattr(token_prices, "http_json", fake_http_json)

    data = token_prices.fetch_token_prices()

    assert [model["id"] for model in data["models"]] == [
        "qwen/qwen-2.5-7b-instruct",
        "meta-llama/llama-3.1-8b-instruct",
    ]
    assert data["models"][0]["usd_per_million_output"] == pytest.approx(0.05)


def test_fetch_token_prices_uses_cached_result_within_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_token_price_cache()
    calls = 0
    payload = openrouter_payload(
        {
            "id": "meta-llama/llama-3.1-8b-instruct",
            "name": "Llama 3.1 8B Instruct",
            "pricing": {"completion": "0.00000012", "prompt": "0.00000006"},
        }
    )

    def fake_http_json(url: str) -> dict:
        nonlocal calls
        assert url == token_prices.API
        calls += 1
        return payload

    monkeypatch.setattr(token_prices, "http_json", fake_http_json)
    monkeypatch.setattr(token_prices.time, "time", lambda: 1_000.0)

    first = token_prices.fetch_token_prices()
    second = token_prices.fetch_token_prices()

    assert first is second
    assert calls == 1
