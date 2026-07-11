from datetime import UTC, datetime, timedelta

import pytest

from gpu_econ.backtesting import HistoricalQuote, backtest_decision

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def quote(hours: int, alternative: str, cost: float) -> HistoricalQuote:
    return HistoricalQuote(T0 + timedelta(hours=hours), alternative, cost)


def test_fixed_decision_hindsight_and_regret() -> None:
    result = backtest_decision(
        [quote(0, "rent", 2), quote(0, "own", 3), quote(2, "rent", 5)],
        decision_at=T0,
        realized_start=T0,
        realized_end=T0 + timedelta(hours=4),
        max_quote_age=timedelta(hours=10),
    )
    assert result.chosen_option == "rent"
    assert result.original_option == "rent"
    assert result.realized_cost == pytest.approx(14)
    assert result.hindsight_best_cost == pytest.approx(10)
    assert result.regret == pytest.approx(4)
    assert result.coverage == 1
    assert not result.incomplete
    assert len(result.time_series_points) == 2


def test_future_quote_cannot_leak_into_decision() -> None:
    result = backtest_decision(
        [quote(0, "own", 3), quote(1, "rent", 1)],
        decision_at=T0,
        realized_start=T0,
        realized_end=T0 + timedelta(hours=2),
        max_quote_age=timedelta(hours=10),
    )
    assert result.chosen_option == "own"


def test_gap_marks_result_incomplete_instead_of_inventing_cost() -> None:
    result = backtest_decision(
        [quote(0, "rent", 2)],
        decision_at=T0,
        realized_start=T0,
        realized_end=T0 + timedelta(hours=4),
        max_quote_age=timedelta(hours=2),
    )
    assert result.coverage == pytest.approx(0.5)
    assert result.incomplete
    assert result.realized_cost is None
    assert result.hindsight_best_cost is None
    assert result.regret is None
    assert result.missing_intervals == ((T0 + timedelta(hours=2), T0 + timedelta(hours=4)),)


def test_non_expiring_alternative_remains_available_during_market_gap() -> None:
    result = backtest_decision(
        [
            HistoricalQuote(T0, "own", 1.0, expires=False),
            HistoricalQuote(T0, "rent", 2.0),
        ],
        decision_at=T0,
        realized_start=T0,
        realized_end=T0 + timedelta(hours=3),
        max_quote_age=timedelta(hours=1),
    )

    assert result.chosen_option == "own"
    assert result.coverage == 1
    assert result.realized_cost == 3
    assert result.incomplete is False


def test_requires_valid_aware_window() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        backtest_decision(
            [],
            decision_at=datetime(2026, 1, 1),
            realized_start=T0,
            realized_end=T0 + timedelta(hours=1),
            max_quote_age=timedelta(hours=1),
        )
