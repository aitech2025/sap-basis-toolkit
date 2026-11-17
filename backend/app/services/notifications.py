import json
import smtplib
from email.message import EmailMessage
from urllib.request import Request, urlopen

from .. import config
from ..config import (
    ALERT_EMAIL_TO, ALERT_WEBHOOK_URL, SMTP_FROM, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT,
    SMTP_STARTTLS, SMTP_USERNAME,
)

SEVERITY_EMOJI = {"critical": ":red_circle:", "error": ":red_circle:", "warning": ":warning:", "info": ":information_source:"}


def _headline(payload: dict) -> str:
    if payload.get("summary"):
        return str(payload["summary"])
    status = str(payload.get("status", "alert")).upper()
    target = payload.get("target_name", "")
    days = payload.get("days_remaining")
    suffix = f" – {days} days remaining" if days is not None else ""
    return f"[SAP Basis Sentinel] {status}: {target}{suffix}"


def _facts(payload: dict) -> list[tuple[str, str]]:
    skip = {"event", "summary", "details"}
    facts = [(k, str(v)) for k, v in payload.items() if k not in skip and v not in (None, "")]
    for k, v in (payload.get("details") or {}).items():
        if v not in (None, ""):
            facts.append((k, str(v)))
    return facts[:20]


def format_webhook_payload(payload: dict, fmt: str | None = None) -> dict:
    """Render an alert for the configured chat tool. 'generic' keeps the raw JSON contract."""
    fmt = (fmt or config.ALERT_WEBHOOK_FORMAT or "generic").lower()
    if fmt == "slack":
        severity = str(payload.get("severity") or payload.get("status") or "info").lower()
        emoji = SEVERITY_EMOJI.get(severity, ":bell:")
        lines = "\n".join(f"*{k}*: {v}" for k, v in _facts(payload))
        return {
            "text": f"{emoji} {_headline(payload)}",
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": f"{emoji} *{_headline(payload)}*"}},
                {"type": "section", "text": {"type": "mrkdwn", "text": lines or "_no details_"}},
            ],
        }
    if fmt == "teams":
        # Microsoft Teams "Workflows" (Power Automate) webhook with an Adaptive Card.
        return {
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {"type": "TextBlock", "text": _headline(payload), "weight": "Bolder", "wrap": True},
                        {"type": "FactSet", "facts": [{"title": k, "value": v} for k, v in _facts(payload)]},
                    ],
                },
            }],
        }
    return payload


def _post_json(url: str, body: dict) -> tuple[bool, str | None]:
    request = Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=10) as response:
            if response.status >= 300:
                return False, f"HTTP {response.status}"
        return True, None
    except Exception as exc:
        return False, str(exc)[:1000]


def send_webhook(payload: dict) -> tuple[bool, str | None]:
    if not ALERT_WEBHOOK_URL:
        return False, "Webhook not configured"
    return _post_json(ALERT_WEBHOOK_URL, format_webhook_payload(payload))


def pagerduty_event(payload: dict, dedup_key: str, severity: str = "warning") -> dict:
    pd_severity = {"critical": "critical", "error": "error", "warning": "warning"}.get(severity, "info")
    return {
        "routing_key": config.PAGERDUTY_ROUTING_KEY,
        "event_action": "trigger",
        "dedup_key": dedup_key[:255],
        "payload": {
            "summary": _headline(payload)[:1024],
            "source": str(payload.get("hostname") or payload.get("sap_system_id") or "sap-basis-toolkit"),
            "severity": pd_severity,
            "component": str(payload.get("event", "sap-basis-toolkit")),
            "custom_details": dict(_facts(payload)),
        },
    }


def send_pagerduty(payload: dict, dedup_key: str, severity: str = "warning") -> tuple[bool, str | None]:
    if not config.PAGERDUTY_ROUTING_KEY:
        return False, "PagerDuty not configured"
    return _post_json(config.PAGERDUTY_EVENTS_URL, pagerduty_event(payload, dedup_key, severity))


def send_email(subject: str, body: str) -> tuple[bool, str | None]:
    if not SMTP_HOST or not ALERT_EMAIL_TO or not SMTP_FROM:
        return False, "SMTP or recipient configuration is incomplete"
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = SMTP_FROM
    message["To"] = ", ".join(ALERT_EMAIL_TO)
    message.set_content(body)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as client:
            if SMTP_STARTTLS:
                client.starttls()
            if SMTP_USERNAME:
                client.login(SMTP_USERNAME, SMTP_PASSWORD)
            client.send_message(message)
        return True, None
    except Exception as exc:
        return False, str(exc)[:1000]
