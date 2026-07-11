"""Pure state machine for evaluating metric and recommendation alerts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum


class AlertType(str, Enum):
    THRESHOLD = "threshold"
    CHANGE = "change"
    RECOMMENDATION = "recommendation"
    SAVINGS = "savings"
    BREAK_EVEN = "break_even"
    GPU = "gpu"
    CONFIDENCE = "confidence"


class AlertDirection(str, Enum):
    ABOVE = "above"
    BELOW = "below"
    CHANGES = "changes"
    EQUALS = "equals"


@dataclass(frozen=True)
class AlertRule:
    id: str
    type: AlertType
    metric: str
    direction: AlertDirection
    threshold: float | str | None = None
    confirmations: int = 1
    cooldown: timedelta = timedelta(0)
    reset_threshold: float | None = None
    relative_change: bool = False

    def __post_init__(self) -> None:
        if not self.id or not self.metric:
            raise ValueError("id and metric must not be empty")
        if self.confirmations < 1:
            raise ValueError("confirmations must be at least one")
        if self.cooldown < timedelta(0):
            raise ValueError("cooldown must be non-negative")


@dataclass(frozen=True)
class AlertState:
    previous_value: float | str | None = None
    pending_confirmations: int = 0
    active: bool = False
    last_emitted_at: datetime | None = None
    last_dedupe_key: str | None = None


@dataclass(frozen=True)
class AlertEvent:
    rule_id: str
    type: AlertType
    metric: str
    value: float | str
    observed_at: datetime
    dedupe_key: str


def evaluate_alert(
    rule: AlertRule,
    state: AlertState,
    value: float | str,
    observed_at: datetime,
) -> tuple[AlertState, AlertEvent | None]:
    """Evaluate one ordered sample, returning new state and at most one event."""
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    if state.last_emitted_at is not None and observed_at < state.last_emitted_at:
        raise ValueError("samples must be evaluated in time order")
    if rule.direction is AlertDirection.CHANGES:
        return _evaluate_change_alert(rule, state, value, observed_at)

    matched = _matches(rule, state.previous_value, value)
    reset = _is_reset(rule, value, matched)
    if state.active and not reset:
        return replace(state, previous_value=value), None
    if reset:
        state = replace(state, active=False, pending_confirmations=0)
    if not matched:
        return replace(state, previous_value=value, pending_confirmations=0), None

    confirmations = state.pending_confirmations + 1
    next_state = replace(state, previous_value=value, pending_confirmations=confirmations)
    if confirmations < rule.confirmations:
        return next_state, None

    dedupe_key = f"{rule.id}:{value}"
    cooling_down = (
        state.last_emitted_at is not None
        and observed_at - state.last_emitted_at < rule.cooldown
    )
    duplicate = state.active or state.last_dedupe_key == dedupe_key and cooling_down
    if cooling_down or duplicate:
        return replace(next_state, active=True), None

    event = AlertEvent(rule.id, rule.type, rule.metric, value, observed_at, dedupe_key)
    return (
        replace(
            next_state,
            active=True,
            pending_confirmations=0,
            last_emitted_at=observed_at,
            last_dedupe_key=dedupe_key,
        ),
        event,
    )


def _evaluate_change_alert(
    rule: AlertRule,
    state: AlertState,
    value: float | str,
    observed_at: datetime,
) -> tuple[AlertState, AlertEvent | None]:
    """Confirm that a changed value persists across multiple observations."""
    previous = state.previous_value
    if previous is None:
        return replace(state, previous_value=value), None
    if state.active and value == previous:
        return state, None
    if state.active:
        state = replace(state, active=False, pending_confirmations=0)

    if not _matches(rule, previous, value):
        return replace(state, previous_value=value, pending_confirmations=0), None
    confirmations = state.pending_confirmations + 1
    next_state = replace(state, pending_confirmations=confirmations)
    if confirmations < rule.confirmations:
        return next_state, None

    dedupe_key = f"{rule.id}:{value}"
    cooling_down = (
        state.last_emitted_at is not None
        and observed_at - state.last_emitted_at < rule.cooldown
    )
    if cooling_down:
        return replace(next_state, previous_value=value, active=True), None
    event = AlertEvent(rule.id, rule.type, rule.metric, value, observed_at, dedupe_key)
    return (
        replace(
            next_state,
            previous_value=value,
            active=True,
            pending_confirmations=0,
            last_emitted_at=observed_at,
            last_dedupe_key=dedupe_key,
        ),
        event,
    )


def _matches(rule: AlertRule, previous: float | str | None, value: float | str) -> bool:
    if rule.direction is AlertDirection.CHANGES:
        if previous is None:
            return False
        if rule.threshold is None:
            return value != previous
        before, after, threshold = float(previous), float(value), float(rule.threshold)
        change = abs(after - before)
        if rule.relative_change:
            change = change / abs(before) if before else (float("inf") if change else 0.0)
        return change >= threshold
    if rule.direction is AlertDirection.EQUALS:
        return value == rule.threshold
    number, threshold = float(value), float(rule.threshold)
    if rule.direction is AlertDirection.ABOVE:
        return number >= threshold
    return number <= threshold


def _is_reset(rule: AlertRule, value: float | str, matched: bool) -> bool:
    if not matched and rule.reset_threshold is None:
        return True
    if rule.reset_threshold is None or rule.direction in {
        AlertDirection.CHANGES,
        AlertDirection.EQUALS,
    }:
        return False
    number = float(value)
    if rule.direction is AlertDirection.ABOVE:
        return number <= rule.reset_threshold
    return number >= rule.reset_threshold
