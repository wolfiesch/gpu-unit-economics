import pytest
import web.store as store_module
from web.providers import PriceQuote
from web.store import PriceStore


def quote(
    gpu: str,
    price_per_hour: float,
    *,
    provider: str = "runpod",
    kind: str = "community",
    detail: str = "test fixture",
) -> PriceQuote:
    return PriceQuote(
        provider=provider,
        gpu=gpu,
        price_per_hour=price_per_hour,
        kind=kind,
        source_url=f"https://example.test/{provider}",
        detail=detail,
    )


def test_get_latest_fetches_fresh_quotes_into_empty_store(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = 1_700_000_000.0
    quotes = [
        quote("H100", 1.7, kind="secure", detail="H100 SXM 80GB"),
        quote("B200", 3.4, detail="B200 180GB"),
    ]

    monkeypatch.setattr(store_module.time, "time", lambda: now)
    monkeypatch.setattr(store_module, "fetch_all", lambda: (quotes, []))

    price_store = PriceStore(db_path=tmp_path / "prices.db", ttl_s=60)

    assert price_store.get_latest() == {
        "prices": [q.to_dict() for q in quotes],
        "fetched_at": now,
        "age_seconds": 0,
        "ttl_seconds": 60,
        "stale": False,
        "errors": [],
    }


def test_get_latest_uses_cache_within_ttl_without_refetching(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_time = 1_700_000_100.0
    calls = 0
    cached_quote = quote("H100", 1.7, kind="secure", detail="H100 SXM 80GB")

    def fake_time() -> float:
        return current_time

    def fake_fetch_all() -> tuple[list[PriceQuote], list[str]]:
        nonlocal calls
        calls += 1
        return [cached_quote], []

    monkeypatch.setattr(store_module.time, "time", fake_time)
    monkeypatch.setattr(store_module, "fetch_all", fake_fetch_all)

    price_store = PriceStore(db_path=tmp_path / "prices.db", ttl_s=60)
    first = price_store.get_latest()
    current_time = 1_700_000_105.0
    second = price_store.get_latest()

    assert calls == 1
    assert first["prices"] == [cached_quote.to_dict()]
    assert second == {
        "prices": [cached_quote.to_dict()],
        "fetched_at": 1_700_000_100.0,
        "age_seconds": 5,
        "ttl_seconds": 60,
        "stale": False,
        "errors": [],
    }


def test_get_latest_force_refetches_even_with_fresh_cache(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_time = 1_700_000_200.0
    calls = 0

    def fake_time() -> float:
        return current_time

    def fake_fetch_all() -> tuple[list[PriceQuote], list[str]]:
        nonlocal calls
        calls += 1
        return [quote("H100", float(calls), kind="secure", detail=f"fetch {calls}")], []

    monkeypatch.setattr(store_module.time, "time", fake_time)
    monkeypatch.setattr(store_module, "fetch_all", fake_fetch_all)

    price_store = PriceStore(db_path=tmp_path / "prices.db", ttl_s=60)
    price_store.get_latest()
    current_time = 1_700_000_210.0

    assert price_store.get_latest(force=True) == {
        "prices": [
            quote("H100", 2.0, kind="secure", detail="fetch 2").to_dict(),
        ],
        "fetched_at": 1_700_000_210.0,
        "age_seconds": 0,
        "ttl_seconds": 60,
        "stale": False,
        "errors": [],
    }
    assert calls == 2


def test_get_latest_keeps_last_snapshot_when_forced_refetch_returns_only_errors(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_time = 1_700_000_300.0
    calls = 0
    cached_quote = quote("B200", 3.4, detail="B200 180GB")

    def fake_time() -> float:
        return current_time

    def fake_fetch_all() -> tuple[list[PriceQuote], list[str]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return [cached_quote], []
        return [], ["boom"]

    monkeypatch.setattr(store_module.time, "time", fake_time)
    monkeypatch.setattr(store_module, "fetch_all", fake_fetch_all)

    price_store = PriceStore(db_path=tmp_path / "prices.db", ttl_s=60)
    price_store.get_latest()
    current_time = 1_700_000_400.0

    assert price_store.get_latest(force=True) == {
        "prices": [cached_quote.to_dict()],
        "fetched_at": 1_700_000_300.0,
        "age_seconds": 100,
        "ttl_seconds": 60,
        "stale": True,
        "errors": ["boom"],
    }
    assert calls == 2


def test_history_returns_time_windowed_rows_for_canonical_gpu(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_time = 1_700_000_500.0
    h100_quote = quote(
        "H100",
        2.75,
        provider="vast.ai",
        kind="on-demand",
        detail="H100 PCIE, EU-DE",
    )
    b200_quote = quote("B200", 6.25, provider="vast.ai", kind="on-demand")

    def fake_time() -> float:
        return current_time

    monkeypatch.setattr(store_module.time, "time", fake_time)
    monkeypatch.setattr(store_module, "fetch_all", lambda: ([h100_quote, b200_quote], []))

    price_store = PriceStore(db_path=tmp_path / "prices.db", ttl_s=60)
    price_store.get_latest()
    current_time = 1_700_000_501.0

    assert price_store.history("H100") == [
        {
            "fetched_at": 1_700_000_500.0,
            "provider": "vast.ai",
            "price_per_hour": 2.75,
            "kind": "on-demand",
        }
    ]
    assert price_store.history("H100", hours=0) == []
