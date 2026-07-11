from datetime import UTC, datetime, timedelta

from gpu_econ.alerts import (
    AlertDirection,
    AlertRule,
    AlertState,
    AlertType,
    evaluate_alert,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_boundary_confirmations_reset_and_retrigger() -> None:
    rule = AlertRule("cheap", AlertType.THRESHOLD, "price", AlertDirection.BELOW, 2, 2)
    state = AlertState()
    state, event = evaluate_alert(rule, state, 1.9, T0)
    assert event is None
    state, event = evaluate_alert(rule, state, 1.8, T0 + timedelta(minutes=1))
    assert event is not None
    state, event = evaluate_alert(rule, state, 1.7, T0 + timedelta(minutes=2))
    assert event is None
    state, _ = evaluate_alert(rule, state, 2.1, T0 + timedelta(minutes=3))
    state, event = evaluate_alert(rule, state, 1.8, T0 + timedelta(minutes=4))
    assert event is None
    state, event = evaluate_alert(rule, state, 1.7, T0 + timedelta(minutes=5))
    assert event is not None


def test_hysteresis_requires_reset_threshold() -> None:
    rule = AlertRule(
        "savings", AlertType.SAVINGS, "savings", AlertDirection.ABOVE, 100, reset_threshold=80
    )
    state, event = evaluate_alert(rule, AlertState(), 100, T0)
    assert event is not None
    state, _ = evaluate_alert(rule, state, 90, T0 + timedelta(minutes=1))
    state, event = evaluate_alert(rule, state, 101, T0 + timedelta(minutes=2))
    assert event is None
    state, _ = evaluate_alert(rule, state, 79, T0 + timedelta(minutes=3))
    state, event = evaluate_alert(rule, state, 101, T0 + timedelta(minutes=4))
    assert event is not None


def test_cooldown_suppresses_retrigger_after_reset() -> None:
    rule = AlertRule(
        "confidence",
        AlertType.CONFIDENCE,
        "confidence",
        AlertDirection.BELOW,
        0.5,
        cooldown=timedelta(hours=1),
    )
    state, event = evaluate_alert(rule, AlertState(), 0.4, T0)
    assert event is not None
    state, _ = evaluate_alert(rule, state, 0.8, T0 + timedelta(minutes=1))
    state, event = evaluate_alert(rule, state, 0.4, T0 + timedelta(minutes=2))
    assert event is None
    state, _ = evaluate_alert(rule, state, 0.8, T0 + timedelta(hours=1))
    state, event = evaluate_alert(rule, state, 0.4, T0 + timedelta(hours=1, minutes=1))
    assert event is not None


def test_recommendation_gpu_and_change_types() -> None:
    for alert_type in (AlertType.RECOMMENDATION, AlertType.GPU):
        rule = AlertRule(alert_type.value, alert_type, "choice", AlertDirection.EQUALS, "H200")
        _, event = evaluate_alert(rule, AlertState(), "H200", T0)
        assert event is not None

    rule = AlertRule("move", AlertType.CHANGE, "price", AlertDirection.CHANGES, 0.10)
    state, event = evaluate_alert(rule, AlertState(previous_value=10.0), 10.05, T0)
    assert event is None
    _, event = evaluate_alert(rule, state, 10.2, T0 + timedelta(minutes=1))
    assert event is not None


def test_recommendation_change_can_require_stable_confirmations() -> None:
    rule = AlertRule(
        "recommendation",
        AlertType.RECOMMENDATION,
        "choice",
        AlertDirection.CHANGES,
        confirmations=3,
    )
    state, _ = evaluate_alert(rule, AlertState(), "rent:H100", T0)
    state, event = evaluate_alert(rule, state, "own:H200", T0 + timedelta(minutes=1))
    assert event is None
    state, event = evaluate_alert(rule, state, "own:H200", T0 + timedelta(minutes=2))
    assert event is None
    state, event = evaluate_alert(rule, state, "own:H200", T0 + timedelta(minutes=3))
    assert event is not None


def test_break_even_type_uses_same_numeric_boundary_engine() -> None:
    rule = AlertRule("be", AlertType.BREAK_EVEN, "break_even", AlertDirection.ABOVE, 0.75)
    _, event = evaluate_alert(rule, AlertState(), 0.75, T0)
    assert event is not None
