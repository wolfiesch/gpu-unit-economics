from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

import pytest
from web.intelligence_store import IntelligenceStore
from web.notifications import (
    DeliveryError,
    SMTPConfig,
    deliver_pending,
    send_email,
    send_webhook,
    validate_email,
    validate_webhook_url,
)


def public_resolver(host, port, type):  # noqa: A002, ARG001
    return [(2, 1, 6, "", ("8.8.8.8", port))]


def private_resolver(host, port, type):  # noqa: A002, ARG001
    return [(2, 1, 6, "", ("127.0.0.1", port))]


def sample_delivery(channel: str = "webhook") -> dict:
    return {
        "id": "delivery-1",
        "event_id": "event-1",
        "rule_id": "rule-1",
        "channel": channel,
        "target": "https://example.com/alerts" if channel == "webhook" else "user@example.com",
        "gpu": "H100",
        "alert_type": "price_below",
        "explanation": "H100 crossed below $2.00 per hour.",
        "value": 1.8,
        "previous_value": 2.1,
        "event_created_at": 100.0,
        "context": {"fetched_at": 100.0},
        "delivery_secret": "signing-secret",
    }


def test_destination_validation_rejects_unsafe_values() -> None:
    assert validate_email("user@example.com") == "user@example.com"
    with pytest.raises(ValueError):
        validate_email("User <user@example.com>")
    with pytest.raises(ValueError, match="HTTPS"):
        validate_webhook_url("http://example.com/hook", resolver=public_resolver)
    with pytest.raises(ValueError, match="public IP"):
        validate_webhook_url("https://localhost/hook", resolver=private_resolver)


def test_signed_webhook_has_stable_payload_and_signature(monkeypatch) -> None:
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def getcode(self):
            return 204

    class Opener:
        def open(self, request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return Response()

    monkeypatch.setattr("web.notifications.validate_webhook_url", lambda value: value)
    monkeypatch.setattr("web.notifications.urllib.request.build_opener", lambda *args: Opener())
    monkeypatch.setattr("web.notifications.time.time", lambda: 1234)

    assert send_webhook(sample_delivery()) == 204
    request = captured["request"]
    headers = {key.lower(): value for key, value in request.header_items()}
    expected = hmac.new(
        b"signing-secret", b"1234." + request.data, hashlib.sha256
    ).hexdigest()
    assert headers["x-gpu-econ-signature"] == f"sha256={expected}"
    assert headers["x-gpu-econ-timestamp"] == "1234"
    assert captured["timeout"] == 10


def test_email_uses_tls_and_plain_text_message(monkeypatch) -> None:
    captured = {}

    class SMTP:
        def __init__(self, host, port, timeout):
            captured.update(host=host, port=port, timeout=timeout)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self, context):
            captured["tls"] = context is not None

        def login(self, username, password):
            captured["login"] = (username, password)

        def send_message(self, message):
            captured["message"] = message

    monkeypatch.setattr("web.notifications.smtplib.SMTP", SMTP)
    config = SMTPConfig("smtp.example.com", 587, "user", "pass", "alerts@example.com", True, False)

    assert send_email(sample_delivery("email"), config) == 250
    assert captured["tls"] is True
    assert captured["login"] == ("user", "pass")
    assert captured["message"]["To"] == "user@example.com"
    assert "H100 crossed below" in captured["message"].get_content()


def test_delivery_worker_records_retry_and_success(tmp_path: Path) -> None:
    store = IntelligenceStore(tmp_path / "alerts.db")
    rule = store.create_rule(
        gpu="H100",
        alert_type="price_below",
        threshold=2,
        delivery_channel="webhook",
        delivery_target="https://example.com/alerts",
        delivery_secret="secret",
    )
    store.commit_evaluation(
        rule_id=rule["id"],
        previous_state={},
        new_state={"active": True},
        event={"value": 1.8, "explanation": "alert", "dedupe_key": "one"},
    )

    failed = deliver_pending(
        store,
        webhook_sender=lambda delivery: (_ for _ in ()).throw(DeliveryError("offline")),
    )
    assert failed == {"claimed": 1, "delivered": 0, "failed": 1}
    assert store.list_deliveries()[0]["status"] == "retry"

