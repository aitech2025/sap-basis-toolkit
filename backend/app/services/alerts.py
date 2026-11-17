"""Module-agnostic alerting with de-duplication, used by the toolkit modules.

The certificate endpoint monitor keeps its own target-scoped history
(notification_events); every other module records alerts in alert_events.
"""
from datetime import datetime, timezone

from sqlalchemy import select

from .. import config
from ..db import alert_events, get_db
from . import notifications


def _channels(payload: dict, fingerprint: str, severity: str):
    channels = []
    if config.ALERT_WEBHOOK_URL:
        channels.append(("webhook", lambda: notifications.send_webhook(payload)))
    if config.SMTP_HOST and config.ALERT_EMAIL_TO:
        body = "\n".join(f"{k}: {v}" for k, v in notifications._facts(payload))
        channels.append(("email", lambda: notifications.send_email(payload["summary"], body)))
    if config.PAGERDUTY_ROUTING_KEY and severity in ("critical", "error"):
        channels.append(("pagerduty", lambda: notifications.send_pagerduty(payload, fingerprint, severity)))
    return channels


def send_alert_once(module: str, fingerprint: str, summary: str, severity: str = "warning",
                    details: dict | None = None, system_id: int | None = None,
                    incident_key: str | None = None) -> list[dict]:
    """Send an alert to every configured channel unless this fingerprint was already delivered."""
    payload = {
        "event": f"sap-basis-toolkit.{module}",
        "summary": f"[SAP Basis Toolkit] {summary}",
        "severity": severity,
        "details": details or {},
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }
    outcomes = []
    from . import incidents
    try:
        incidents.raise_module_incident(system_id, f"{module}:{incident_key or fingerprint}", summary, severity, details)
    except Exception:  # incident bookkeeping must never block notifications
        pass
    system = incidents._system(system_id)
    window = incidents.active_maintenance(system)
    for channel, sender in _channels(payload, fingerprint, severity):
        if window:
            outcomes.append({"channel": channel, "result": "suppressed", "error": f"maintenance: {window['reason']}"})
            continue
        with get_db() as connection:
            already = connection.execute(
                select(alert_events.c.id).where(
                    alert_events.c.fingerprint == fingerprint,
                    alert_events.c.channel == channel,
                    alert_events.c.result == "sent",
                ).limit(1)
            ).first()
        if already:
            outcomes.append({"channel": channel, "result": "suppressed"})
            continue
        ok, error = sender()
        with get_db() as connection:
            connection.execute(alert_events.insert().values(
                system_id=system_id, module=module, fingerprint=fingerprint[:300], channel=channel, severity=severity,
                summary=payload["summary"], result="sent" if ok else "failed", error=error,
            ))
        outcomes.append({"channel": channel, "result": "sent" if ok else "failed", "error": error})
    return outcomes
