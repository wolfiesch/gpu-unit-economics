import csv

import pytest
from web import historical


def reset_historical_cache() -> None:
    historical._cache["rows"] = None
    historical._cache["skipped"] = 0


def real_header() -> list[str]:
    with historical.DATA_PATH.open(newline="", encoding="utf-8") as fh:
        return next(csv.reader(fh))


def write_historical_csv(path, rows: list[dict[str, str]]) -> None:
    fieldnames = real_header()
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def synthetic_row(**overrides: str) -> dict[str, str]:
    row = {
        "sku": "NVIDIA TEST GPU",
        "vendor": "NVIDIA",
        "track": "current_ai_sku",
        "market_segment": "enterprise",
        "price_type": "launch_price",
        "condition": "new",
        "date": "2025-01-01",
        "period_label": "test",
        "currency": "USD",
        "nominal_price": "12345",
        "usd_nominal": "12345",
        "usd_2026": "13000.5",
        "source_id": "test_fixture",
        "confidence": "A",
        "sample_count": "1",
        "notes": "Synthetic fixture row.",
    }
    row.update(overrides)
    return row


def test_load_rows_reads_all_production_rows_as_numeric_prices() -> None:
    reset_historical_cache()

    rows = historical.load_rows()

    assert len(rows) == 30
    assert all(isinstance(row["usd_nominal"], float) for row in rows)
    assert all(isinstance(row["usd_2026"], float) for row in rows)


def test_table_rows_omit_unparseable_nominal_price_column() -> None:
    reset_historical_cache()

    payload = historical.table()

    assert all("nominal_price" not in row for row in payload["rows"])


def test_table_exposes_track_enum_and_cpi_base_metadata() -> None:
    reset_historical_cache()

    payload = historical.table()

    assert payload["tracks"] == [
        "current_ai_sku",
        "enterprise_pre_llm",
        "consumer_crypto_proxy",
    ]
    assert payload["cpi_base"] == {"month": "2026-05-01", "cpi": 333.979}


def test_load_rows_skips_rows_with_unparseable_nominal_usd(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_csv = tmp_path / "historical_gpu_prices.csv"
    write_historical_csv(
        tmp_csv,
        [
            synthetic_row(sku="Valid GPU", usd_nominal="12345"),
            synthetic_row(sku="Range GPU", usd_nominal="60000-70000"),
        ],
    )
    monkeypatch.setattr(historical, "DATA_PATH", tmp_csv)
    reset_historical_cache()

    rows = historical.load_rows()

    assert len(rows) == 1
    assert rows[0]["sku"] == "Valid GPU"
    assert rows[0]["usd_nominal"] == 12345.0
    assert historical._cache["skipped"] == 1


def test_load_rows_maps_blank_real_usd_to_none(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_csv = tmp_path / "historical_gpu_prices.csv"
    write_historical_csv(
        tmp_csv,
        [synthetic_row(sku="Blank CPI GPU", usd_nominal="20000", usd_2026="")],
    )
    monkeypatch.setattr(historical, "DATA_PATH", tmp_csv)
    reset_historical_cache()

    rows = historical.load_rows()

    assert len(rows) == 1
    assert rows[0]["sku"] == "Blank CPI GPU"
    assert rows[0]["usd_2026"] is None
