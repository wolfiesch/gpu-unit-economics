"""Durable alert delivery through SMTP email and signed HTTPS webhooks."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import smtplib
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any

from .intelligence_store import IntelligenceStore


class DeliveryError(RuntimeError):
    """A delivery failed and may be retried."""

    def __init__(self, message: str, response_code: int | None = None) -> None:
        super().__init__(message)
        self.response_code = response_code


@dataclass(frozen=True)
class SMTPConfig:
    host: str
    port: int
    username: str
    password: str
    sender: str
    use_tls: bool
    use_ssl: bool

    @classmethod
    def from_env(cls) -> SMTPConfig | None:
        host = os.environ.get("SMTP_HOST", "").strip()
        sender = os.environ.get("SMTP_FROM", "").strip()
        if not host or not sender:
            return None
        return cls(
            host=host,
            port=int(os.environ.get("SMTP_PORT", "587")),
            username=os.environ.get("SMTP_USERNAME", ""),
            password=os.environ.get("SMTP_PASSWORD", ""),
            sender=sender,
            use_tls=os.environ.get("SMTP_USE_TLS", "true").lower() in {"1", "true", "yes"},
            use_ssl=os.environ.get("SMTP_USE_SSL", "false").lower() in {"1", "true", "yes"},
        )


def email_configured() -> bool:
    return SMTPConfig.from_env() is not None


def validate_email(address: str) -> str:
    address = address.strip()
    _, parsed = parseaddr(address)
    if parsed != address or len(address) > 254 or "@" not in address:
        raise ValueError("enter one valid email address")
    local, domain = address.rsplit("@", 1)
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise ValueError("enter one valid email address")
    return address


def validate_webhook_url(
    url: str,
    *,
    resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo,
) -> str:
    """Require HTTPS and reject hosts resolving to private or special-use networks."""
    url = url.strip()
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("webhook URL must use HTTPS")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("webhook URL cannot contain credentials or a fragment")
    try:
        addresses = resolver(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("webhook hostname could not be resolved") from exc
    if not addresses:
        raise ValueError("webhook hostname could not be resolved")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("webhook hostname must resolve only to public IP addresses")
    return url


def _payload(delivery: dict[str, Any]) -> bytes:
    return json.dumps(
        {
            "version": "1",
            "event_id": delivery["event_id"],
            "rule_id": delivery["rule_id"],
            "gpu": delivery["gpu"],
            "alert_type": delivery["alert_type"],
            "explanation": delivery["explanation"],
            "value": delivery["value"],
            "previous_value": delivery["previous_value"],
            "created_at": delivery["event_created_at"],
            "context": delivery["context"],
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def send_email(delivery: dict[str, Any], config: SMTPConfig | None = None) -> int:
    config = config or SMTPConfig.from_env()
    if config is None:
        raise DeliveryError("SMTP is not configured on the server")
    target = validate_email(delivery["target"])
    message = EmailMessage()
    message["Subject"] = f"GPU alert: {delivery['gpu']} {delivery['alert_type'].replace('_', ' ')}"
    message["From"] = config.sender
    message["To"] = target
    message.set_content(
        f"{delivery['explanation']}\n\n"
        f"GPU: {delivery['gpu']}\n"
        f"Alert: {delivery['alert_type']}\n"
        f"Event ID: {delivery['event_id']}\n"
        "Dashboard: https://gpu.wolfie.gg/#intelligence\n"
    )
    smtp_class = smtplib.SMTP_SSL if config.use_ssl else smtplib.SMTP
    context = ssl.create_default_context()
    try:
        with smtp_class(config.host, config.port, timeout=15) as smtp:
            if config.use_tls and not config.use_ssl:
                smtp.starttls(context=context)
            if config.username:
                smtp.login(config.username, config.password)
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise DeliveryError(f"email delivery failed: {exc}") from exc
    return 250


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def send_webhook(delivery: dict[str, Any]) -> int:
    target = validate_webhook_url(delivery["target"])
    body = _payload(delivery)
    timestamp = str(int(time.time()))
    signature = hmac.new(
        delivery["delivery_secret"].encode(),
        timestamp.encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    request = urllib.request.Request(
        target,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "gpu-unit-economics-alerts/1.0",
            "X-GPU-Econ-Event": delivery["event_id"],
            "X-GPU-Econ-Timestamp": timestamp,
            "X-GPU-Econ-Signature": f"sha256={signature}",
        },
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=10) as response:
            code = response.getcode()
    except urllib.error.HTTPError as exc:
        raise DeliveryError(f"webhook returned HTTP {exc.code}", exc.code) from exc
    except (OSError, urllib.error.URLError) as exc:
        raise DeliveryError(f"webhook delivery failed: {exc}") from exc
    if not 200 <= code < 300:
        raise DeliveryError(f"webhook returned HTTP {code}", code)
    return code


def deliver_pending(
    store: IntelligenceStore,
    *,
    email_sender: Callable[[dict[str, Any]], int] = send_email,
    webhook_sender: Callable[[dict[str, Any]], int] = send_webhook,
    limit: int = 20,
) -> dict[str, Any]:
    """Claim due work, deliver it once, and record retry or terminal state."""
    claimed = store.claim_deliveries(limit=limit)
    delivered = 0
    failed = 0
    for item in claimed:
        sender = email_sender if item["channel"] == "email" else webhook_sender
        try:
            response_code = sender(item)
        except DeliveryError as exc:
            failed += 1
            store.finish_delivery(
                item["id"],
                success=False,
                error=str(exc),
                response_code=exc.response_code,
            )
        except Exception as exc:  # noqa: BLE001 - queue must survive provider bugs
            failed += 1
            store.finish_delivery(item["id"], success=False, error=str(exc))
        else:
            delivered += 1
            store.finish_delivery(
                item["id"], success=True, response_code=response_code
            )
    return {"claimed": len(claimed), "delivered": delivered, "failed": failed}
