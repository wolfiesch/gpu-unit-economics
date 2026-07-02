import sqlite3

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
    region: str = "",
) -> PriceQuote:
    return PriceQuote(
        provider=provider,
        gpu=gpu,
        price_per_hour=price_per_hour,
        kind=kind,
        source_url=f"https://example.test/{provider}",
        detail=detail,
        region=region,
    )


def insert_snapshot(
    db_path,
    *,
    fetched_at: float,
    price_per_hour: float,
    provider: str = "runpod",
    gpu: str = "H100",
    kind: str = "community",
    region: str = "us-east-1",
    detail: str = "test fixture",
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO price_snapshots"
            " (fetched_at, provider, gpu, price_per_hour, kind, source_url, detail, region)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fetched_at,
                provider,
                gpu,
                price_per_hour,
                kind,
                f"https://example.test/{provider}",
                detail,
                region,
            ),
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
            "region": "",
        }
    ]
    assert price_store.history("H100", hours=0) == []


def test_spread_history_returns_per_batch_region_price_ranges(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_time = 1_700_001_000.0
    batches = [
        [
            quote("H100", 3.2, provider="vast.ai", region="US-CA"),
            quote("H100", 2.75, provider="vast.ai", region="EU-DE"),
            quote("H100", 1.0, provider="legacy", region=""),
            quote("B200", 6.25, provider="vast.ai", region="US-NY"),
        ],
        [
            quote("H100", 3.1, provider="vast.ai", region="US-CA"),
            quote("H100", 2.9, provider="vast.ai", region="EU-DE"),
            quote("H100", 3.6, provider="vast.ai", region="US-NY"),
        ],
    ]

    def fake_time() -> float:
        return current_time

    def fake_fetch_all() -> tuple[list[PriceQuote], list[str]]:
        return batches.pop(0), []

    monkeypatch.setattr(store_module.time, "time", fake_time)
    monkeypatch.setattr(store_module, "fetch_all", fake_fetch_all)

    price_store = PriceStore(db_path=tmp_path / "prices.db", ttl_s=60)
    price_store.get_latest(force=True)
    current_time = 1_700_001_300.0
    price_store.get_latest(force=True)
    current_time = 1_700_001_301.0

    assert price_store.spread_history("H100", hours=1) == [
        {
            "fetched_at": 1_700_001_000.0,
            "min_price": 2.75,
            "max_price": 3.2,
            "regions": 2,
        },
        {
            "fetched_at": 1_700_001_300.0,
            "min_price": 2.9,
            "max_price": 3.6,
            "regions": 3,
        },
    ]
    assert price_store.spread_history("H100", hours=0) == []


def test_existing_store_without_region_column_migrates_and_appends_region_rows(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "legacy-prices.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE price_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fetched_at REAL NOT NULL,
                provider TEXT NOT NULL,
                gpu TEXT NOT NULL,
                price_per_hour REAL NOT NULL,
                kind TEXT NOT NULL,
                source_url TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX idx_snapshots_time ON price_snapshots (fetched_at);
            CREATE INDEX idx_snapshots_gpu ON price_snapshots (gpu, provider, fetched_at);
            """
        )
        conn.execute(
            "INSERT INTO price_snapshots"
            " (fetched_at, provider, gpu, price_per_hour, kind, source_url, detail)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                1_700_002_000.0,
                "legacy-provider",
                "H100",
                2.5,
                "spot",
                "https://example.test/legacy",
                "legacy detail",
            ),
        )

    price_store = PriceStore(db_path=db_path, ttl_s=60)

    with sqlite3.connect(db_path) as conn:
        assert {
            row[1] for row in conn.execute("PRAGMA table_info(price_snapshots)")
        } >= {"region"}

    rows, batch_time = price_store.latest_batch()
    assert batch_time == 1_700_002_000.0
    assert rows == [
        {
            "provider": "legacy-provider",
            "gpu": "H100",
            "price_per_hour": 2.5,
            "kind": "spot",
            "source_url": "https://example.test/legacy",
            "detail": "legacy detail",
            "region": "",
        }
    ]

    current_time = 1_700_002_060.0
    fresh_quotes = [
        quote("H100", 2.25, provider="vast.ai", kind="on-demand", region="US-CA"),
        quote("H100", 2.1, provider="vast.ai", kind="on-demand", region="EU-DE"),
    ]

    monkeypatch.setattr(store_module.time, "time", lambda: current_time)
    monkeypatch.setattr(store_module, "fetch_all", lambda: (fresh_quotes, []))

    assert price_store.get_latest(force=True) == {
        "prices": [q.to_dict() for q in fresh_quotes],
        "fetched_at": 1_700_002_060.0,
        "age_seconds": 0,
        "ttl_seconds": 60,
        "stale": False,
        "errors": [],
    }
    assert price_store.history("H100", hours=1) == [
        {
            "fetched_at": 1_700_002_000.0,
            "provider": "legacy-provider",
            "price_per_hour": 2.5,
            "kind": "spot",
            "region": "",
        },
        {
            "fetched_at": 1_700_002_060.0,
            "provider": "vast.ai",
            "price_per_hour": 2.25,
            "kind": "on-demand",
            "region": "US-CA",
        },
        {
            "fetched_at": 1_700_002_060.0,
            "provider": "vast.ai",
            "price_per_hour": 2.1,
            "kind": "on-demand",
            "region": "EU-DE",
        },
    ]


def test_prune_keeps_hourly_cheapest_old_rows_per_gpu_provider_region(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "prices.db"
    price_store = PriceStore(db_path=db_path, ttl_s=60)
    old_hour = 1_700_000_000.0
    now = old_hour + 31 * 24 * 3600
    recent = now - 3600
    rows = [
        (old_hour + 60, 3.0, "us-east-1", "old east expensive"),
        (old_hour + 120, 2.0, "us-east-1", "old east cheapest"),
        (old_hour + 180, 2.5, "us-east-1", "old east middle"),
        (old_hour + 240, 4.0, "us-west-2", "old west expensive"),
        (old_hour + 300, 3.5, "us-west-2", "old west cheapest"),
        (recent, 9.0, "us-east-1", "recent expensive"),
        (recent + 60, 8.0, "us-east-1", "recent cheaper"),
    ]
    for fetched_at, price_per_hour, region, detail in rows:
        insert_snapshot(
            db_path,
            fetched_at=fetched_at,
            price_per_hour=price_per_hour,
            region=region,
            detail=detail,
        )

    monkeypatch.setattr(store_module.time, "time", lambda: now)

    assert price_store.prune(full_days=30) == 3
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        survivors = [
            dict(row)
            for row in conn.execute(
                "SELECT fetched_at, price_per_hour, region, detail"
                " FROM price_snapshots ORDER BY fetched_at, region, price_per_hour"
            )
        ]

    assert survivors == [
        {
            "fetched_at": old_hour + 120,
            "price_per_hour": 2.0,
            "region": "us-east-1",
            "detail": "old east cheapest",
        },
        {
            "fetched_at": old_hour + 300,
            "price_per_hour": 3.5,
            "region": "us-west-2",
            "detail": "old west cheapest",
        },
        {
            "fetched_at": recent,
            "price_per_hour": 9.0,
            "region": "us-east-1",
            "detail": "recent expensive",
        },
        {
            "fetched_at": recent + 60,
            "price_per_hour": 8.0,
            "region": "us-east-1",
            "detail": "recent cheaper",
        },
    ]


def test_get_latest_prunes_successful_refreshes_at_most_once_per_interval(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "prices.db"
    old_hour = 1_700_000_000.0
    current_time = old_hour + 31 * 24 * 3600
    price_store = PriceStore(db_path=db_path, ttl_s=0)

    def count_region(region: str) -> int:
        with sqlite3.connect(db_path) as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM price_snapshots"
                " WHERE fetched_at < ? AND gpu = 'H100' AND provider = 'runpod'"
                " AND region = ?",
                (old_hour + 24 * 3600, region),
            ).fetchone()[0]

    first_old_region = "us-east-1"
    second_old_region = "us-west-2"
    insert_snapshot(
        db_path,
        fetched_at=old_hour + 60,
        price_per_hour=5.0,
        region=first_old_region,
        detail="first expensive",
    )
    insert_snapshot(
        db_path,
        fetched_at=old_hour + 120,
        price_per_hour=4.0,
        region=first_old_region,
        detail="first cheapest",
    )

    refreshes = 0

    def fake_time() -> float:
        return current_time

    def fake_fetch_all() -> tuple[list[PriceQuote], list[str]]:
        nonlocal refreshes
        refreshes += 1
        return [
            quote(
                "B200",
                float(refreshes),
                provider="vast.ai",
                detail=f"refresh {refreshes}",
                region="US-NY",
            )
        ], []

    monkeypatch.setattr(store_module.time, "time", fake_time)
    monkeypatch.setattr(store_module, "fetch_all", fake_fetch_all)

    price_store.get_latest(force=True)
    assert count_region(first_old_region) == 1

    insert_snapshot(
        db_path,
        fetched_at=old_hour + 600,
        price_per_hour=7.0,
        region=second_old_region,
        detail="second expensive",
    )
    insert_snapshot(
        db_path,
        fetched_at=old_hour + 660,
        price_per_hour=6.0,
        region=second_old_region,
        detail="second cheapest",
    )

    current_time += 10
    price_store.get_latest(force=True)
    assert count_region(second_old_region) == 2

    current_time += store_module.PRUNE_INTERVAL_S + 1
    price_store.get_latest(force=True)
    assert count_region(second_old_region) == 1
    assert refreshes == 3


def test_get_latest_returns_fresh_prices_when_best_effort_prune_fails(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current_time = 1_700_003_000.0
    fresh_quote = quote("H100", 2.4, provider="vast.ai", kind="on-demand", region="US-CA")

    def fail_prune(self, full_days: float = store_module.RETENTION_FULL_DAYS) -> int:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store_module.time, "time", lambda: current_time)
    monkeypatch.setattr(store_module, "fetch_all", lambda: ([fresh_quote], []))
    monkeypatch.setattr(PriceStore, "prune", fail_prune)

    price_store = PriceStore(db_path=tmp_path / "prices.db", ttl_s=60)

    assert price_store.get_latest(force=True) == {
        "prices": [fresh_quote.to_dict()],
        "fetched_at": current_time,
        "age_seconds": 0,
        "ttl_seconds": 60,
        "stale": False,
        "errors": [],
    }
