"""Alert delivery channel adapters.

INTERNAL delivery (the persisted alert, visible in the console and API) is
always on. Webhook / Slack / Microsoft Teams / email adapters are optional
and only become active when their destination is configured. A delivery
failure is recorded on the alert; it never raises into the caller and never
blocks ingestion or governance actions.

Secrets (webhook URLs embed tokens) are never logged or echoed back: a
delivery result names the channel, never its URL.
"""
from __future__ import annotations

import json
import logging
import smtplib
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AlertPayload:
    id: str
    kind: str
    severity: str
    title: str
    message: str
    object_type: str | None
    object_id: str | None
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "severity": self.severity, "title": self.title,
                "message": self.message, "object_type": self.object_type, "object_id": self.object_id,
                "details": self.details, "source": "logforge-ai"}


class AlertChannel(ABC):
    name = "abstract"

    @abstractmethod
    def deliver(self, alert: AlertPayload) -> None:
        """Deliver or raise. The bus records the outcome."""


def _post_json(url: str, body: dict[str, Any], timeout: float) -> None:
    if not url.lower().startswith(("https://", "http://")):
        raise ValueError("only http(s) destinations are supported")
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "logforge-ai-alerts"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — scheme validated above
        if resp.status >= 300:
            raise RuntimeError(f"HTTP {resp.status}")


class WebhookChannel(AlertChannel):
    name = "webhook"

    def __init__(self, url: str, timeout: float):
        self.url, self.timeout = url, timeout

    def deliver(self, alert: AlertPayload) -> None:
        _post_json(self.url, alert.as_dict(), self.timeout)


class SlackChannel(AlertChannel):
    name = "slack"

    def __init__(self, url: str, timeout: float):
        self.url, self.timeout = url, timeout

    def deliver(self, alert: AlertPayload) -> None:
        _post_json(self.url, {"text": f"[{alert.severity}] {alert.title}\n{alert.message}"}, self.timeout)


class TeamsChannel(AlertChannel):
    name = "teams"

    def __init__(self, url: str, timeout: float):
        self.url, self.timeout = url, timeout

    def deliver(self, alert: AlertPayload) -> None:
        _post_json(self.url, {"title": f"[{alert.severity}] {alert.title}", "text": alert.message}, self.timeout)


class EmailChannel(AlertChannel):
    name = "email"

    def __init__(self, host: str, port: int, sender: str, recipients: list[str], timeout: float):
        self.host, self.port, self.sender, self.recipients, self.timeout = host, port, sender, recipients, timeout

    def deliver(self, alert: AlertPayload) -> None:
        msg = EmailMessage()
        msg["Subject"] = f"[LogForge {alert.severity}] {alert.title}"
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.recipients)
        msg.set_content(f"{alert.message}\n\n{json.dumps(alert.details, indent=2, default=str)}")
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
            smtp.send_message(msg)


_extra: list[AlertChannel] = []


def register_channel(channel: AlertChannel) -> None:
    """Add a channel programmatically (tests, custom integrations)."""
    _extra.append(channel)


def clear_registered_channels() -> None:
    _extra.clear()


def configured_channels() -> list[AlertChannel]:
    from app.config import get_settings

    s = get_settings()
    t = s.alert_delivery_timeout_seconds
    channels: list[AlertChannel] = []
    if s.alert_webhook_url:
        channels.append(WebhookChannel(s.alert_webhook_url, t))
    if s.alert_slack_webhook_url:
        channels.append(SlackChannel(s.alert_slack_webhook_url, t))
    if s.alert_teams_webhook_url:
        channels.append(TeamsChannel(s.alert_teams_webhook_url, t))
    if s.alert_smtp_host and s.alert_email_from and s.alert_email_to:
        recipients = [r.strip() for r in s.alert_email_to.split(",") if r.strip()]
        channels.append(EmailChannel(s.alert_smtp_host, s.alert_smtp_port, s.alert_email_from, recipients, t))
    return channels + list(_extra)


def channel_status() -> list[dict[str, Any]]:
    """Which adapters exist and whether they are configured (never the destination itself)."""
    from app.config import get_settings

    s = get_settings()
    return [
        {"channel": "internal", "configured": True},
        {"channel": "webhook", "configured": bool(s.alert_webhook_url)},
        {"channel": "slack", "configured": bool(s.alert_slack_webhook_url)},
        {"channel": "teams", "configured": bool(s.alert_teams_webhook_url)},
        {"channel": "email", "configured": bool(s.alert_smtp_host and s.alert_email_from and s.alert_email_to)},
    ]
