"""Pure historical backtesting for hourly alternatives.

The decision is made only from quotes known at ``decision_at``.  Realized cost
then keeps that decision fixed, while the hindsight result may choose the
cheapest covered alternative during each time slice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise


@dataclass(frozen=True, order=True)
class HistoricalQuote:
    observed_at: datetime
    alternative: str
    hourly_cost: float
    expires: bool = True

    def __post_init__(self) -> None:
        if not self.alternative:
            raise ValueError("alternative must not be empty")
        if self.hourly_cost < 0:
            raise ValueError("hourly_cost must be non-negative")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")


@dataclass(frozen=True)
class BacktestPoint:
    start: datetime
    end: datetime
    chosen_option: str | None
    chosen_hourly_cost: float | None
    hindsight_best_option: str | None
    hindsight_hourly_cost: float | None
    realized_cost: float | None
    hindsight_best_cost: float | None


@dataclass(frozen=True)
class BacktestResult:
    decision_at: datetime
    realized_start: datetime
    realized_end: datetime
    original_option: str | None
    chosen_option: str | None
    decision_hourly_cost: float | None
    realized_cost: float | None
    hindsight_best_cost: float | None
    regret: float | None
    coverage: float
    incomplete: bool
    time_series_points: tuple[BacktestPoint, ...]
    missing_intervals: tuple[tuple[datetime, datetime], ...]


def backtest_decision(
    quotes: tuple[HistoricalQuote, ...] | list[HistoricalQuote],
    *,
    decision_at: datetime,
    realized_start: datetime,
    realized_end: datetime,
    max_quote_age: timedelta,
) -> BacktestResult:
    """Backtest the cheapest alternative known as of a historical instant.

    A quote covers time until it is replaced, but never for longer than
    ``max_quote_age``. Costs are for continuous use, so dollars equal hourly
    rate multiplied by covered hours.
    """
    _validate_window(decision_at, realized_start, realized_end, max_quote_age)
    ordered = sorted(quotes)
    known = _latest_by_alternative(ordered, decision_at, max_quote_age)
    chosen = min(known, key=lambda key: (known[key].hourly_cost, key)) if known else None
    decision_cost = known[chosen].hourly_cost if chosen is not None else None

    boundaries = {realized_start, realized_end}
    boundaries.update(
        quote.observed_at
        for quote in ordered
        if realized_start < quote.observed_at < realized_end
    )
    for quote in ordered:
        expiry = quote.observed_at + max_quote_age
        if realized_start < expiry < realized_end:
            boundaries.add(expiry)
    points = sorted(boundaries)

    fixed_total = 0.0
    hindsight_total = 0.0
    covered_seconds = 0.0
    missing: list[tuple[datetime, datetime]] = []
    series: list[BacktestPoint] = []
    for start, end in pairwise(points):
        current = _latest_by_alternative(ordered, start, max_quote_age)
        fixed_quote = current.get(chosen) if chosen is not None else None
        best_quote = min(current.values(), key=lambda quote: quote.hourly_cost) if current else None
        if fixed_quote is None or best_quote is None:
            missing.append((start, end))
            series.append(
                BacktestPoint(start, end, chosen, None, None, None, None, None)
            )
            continue
        hours = (end - start).total_seconds() / 3600
        fixed_slice = fixed_quote.hourly_cost * hours
        hindsight_slice = best_quote.hourly_cost * hours
        fixed_total += fixed_slice
        hindsight_total += hindsight_slice
        covered_seconds += (end - start).total_seconds()
        series.append(
            BacktestPoint(
                start,
                end,
                chosen,
                fixed_quote.hourly_cost,
                best_quote.alternative,
                best_quote.hourly_cost,
                fixed_slice,
                hindsight_slice,
            )
        )

    duration = (realized_end - realized_start).total_seconds()
    coverage = covered_seconds / duration
    complete = chosen is not None and coverage == 1.0
    return BacktestResult(
        decision_at=decision_at,
        realized_start=realized_start,
        realized_end=realized_end,
        original_option=chosen,
        chosen_option=chosen,
        decision_hourly_cost=decision_cost,
        realized_cost=fixed_total if complete else None,
        hindsight_best_cost=hindsight_total if complete else None,
        regret=(fixed_total - hindsight_total) if complete else None,
        coverage=coverage,
        incomplete=not complete,
        time_series_points=tuple(series),
        missing_intervals=tuple(missing),
    )


def _latest_by_alternative(
    quotes: list[HistoricalQuote], at: datetime, max_age: timedelta
) -> dict[str, HistoricalQuote]:
    latest: dict[str, HistoricalQuote] = {}
    for quote in quotes:
        if quote.observed_at > at:
            break
        if not quote.expires or at - quote.observed_at < max_age:
            latest[quote.alternative] = quote
    return latest


def _validate_window(
    decision_at: datetime,
    start: datetime,
    end: datetime,
    max_age: timedelta,
) -> None:
    if any(value.tzinfo is None for value in (decision_at, start, end)):
        raise ValueError("all datetimes must be timezone-aware")
    if start < decision_at:
        raise ValueError("realized_start must be at or after decision_at")
    if end <= start:
        raise ValueError("realized_end must be after realized_start")
    if max_age <= timedelta(0):
        raise ValueError("max_quote_age must be positive")
