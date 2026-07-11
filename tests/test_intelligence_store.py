from pathlib import Path

import pytest
from web.intelligence_store import IntelligenceStore


@pytest.fixture
def store(tmp_path: Path) -> IntelligenceStore:
    return IntelligenceStore(tmp_path / "intelligence.db")


def test_alert_rule_state_and_event_round_trip(store: IntelligenceStore) -> None:
    rule = store.create_rule(
        gpu="H100",
        alert_type="price_below",
        threshold=2.0,
        required_observations=3,
        cooldown_hours=24,
        scenario={"monthly_token_demand": 20_000_000_000},
    )
    assert rule["active"] is True
    assert rule["state"] == {}
    assert rule["scenario"]["monthly_token_demand"] == 20_000_000_000

    state = {"consecutive_matches": 2, "last_value": 1.95}
    store.save_state(rule["id"], state)
    assert store.get_rule(rule["id"])["state"] == state

    event = store.add_event(
        rule_id=rule["id"],
        value=1.9,
        previous_value=2.1,
        explanation="H100 crossed below $2.00 per hour.",
        context={"snapshot_id": "run-1"},
    )
    assert event["context"] == {"snapshot_id": "run-1"}
    assert store.list_events(rule_id=rule["id"])[0]["value"] == 1.9


def test_alert_rules_can_be_paused(store: IntelligenceStore) -> None:
    rule = store.create_rule(
        gpu="B200",
        alert_type="recommendation_change",
        threshold=None,
        required_observations=2,
        cooldown_hours=12,
    )
    store.set_active(rule["id"], False)
    assert store.list_rules(active_only=True) == []
    assert store.get_rule(rule["id"])["active"] is False


def test_evaluation_state_and_event_are_atomic_and_compare_and_swap(
    store: IntelligenceStore,
) -> None:
    rule = store.create_rule(
        gpu="H100",
        alert_type="price_below",
        threshold=2,
    )
    committed, event = store.commit_evaluation(
        rule_id=rule["id"],
        previous_state={},
        new_state={"active": True, "last_observed_at": 100},
        event={
            "value": 1.8,
            "previous_value": 2.1,
            "explanation": "Price crossed below $2.",
            "context": {"fetched_at": 100},
            "dedupe_key": "rule:100",
        },
    )
    assert committed is True
    assert event is not None

    stale_commit, duplicate = store.commit_evaluation(
        rule_id=rule["id"],
        previous_state={},
        new_state={"active": True, "last_observed_at": 100},
        event={
            "explanation": "duplicate",
            "dedupe_key": "rule:100",
        },
    )
    assert stale_commit is False
    assert duplicate is None
    assert len(store.list_events(rule_id=rule["id"])) == 1


def test_missing_rule_raises_key_error(store: IntelligenceStore) -> None:
    with pytest.raises(KeyError):
        store.get_rule("missing")
