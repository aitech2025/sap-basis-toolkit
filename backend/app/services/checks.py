from datetime import datetime, timezone

from sqlalchemy import select

from ..config import ALERT_EMAIL_TO, ALERT_THRESHOLDS_DAYS, ALERT_WEBHOOK_URL, SMTP_HOST
from ..db import certificate_checks, get_db, notification_events, targets
from .certificates import inspect_certificate
from .notifications import send_email, send_webhook
from .target_policy import TargetPolicyError


def check_target(target_id: int) -> dict:
    with get_db() as connection:
        target = connection.execute(
            select(targets).where(targets.c.id == target_id, targets.c.enabled.is_(True))
        ).mappings().first()
    if target is None:
        raise ValueError("Enabled target not found")

    try:
        result = inspect_certificate(target["hostname"], target["port"])
    except TargetPolicyError as exc:
        result = {
            "subject": None, "issuer": None, "serial_number": None,
            "not_before": None, "not_after": None, "days_remaining": None,
            "tls_verified": False, "status": "blocked", "error": str(exc)[:1000],
        }
    except Exception as exc:
        result = {
            "subject": None, "issuer": None, "serial_number": None,
            "not_before": None, "not_after": None, "days_remaining": None,
            "tls_verified": False, "status": "unreachable", "error": str(exc)[:1000],
        }

    with get_db() as connection:
        check = dict(connection.execute(
            certificate_checks.insert().values(
                target_id=target_id, subject=result["subject"], issuer=result["issuer"],
                serial_number=result["serial_number"], not_before=result["not_before"],
                not_after=result["not_after"], days_remaining=result["days_remaining"],
                tls_verified=result["tls_verified"], status=result["status"], error=result["error"],
            ).returning(certificate_checks)
        ).mappings().one())
        last_healthy = connection.execute(
            select(certificate_checks.c.id).where(
                certificate_checks.c.target_id == target_id,
                certificate_checks.c.status.notin_(("unreachable", "blocked")),
            ).order_by(certificate_checks.c.id.desc()).limit(1)
        ).scalar_one_or_none()

    if result["status"] != "blocked":
        dispatch_alerts(dict(target), result, last_healthy)
    return check


def _notification_channels(target: dict, result: dict):
    payload = {
        "event": "certificate.monitoring",
        "target_name": target["name"],
        "hostname": target["hostname"],
        "port": target["port"],
        "status": result.get("status"),
        "days_remaining": result.get("days_remaining"),
        "not_after": result.get("not_after"),
        "subject": result.get("subject"),
        "error": result.get("error"),
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }
    channels = []
    if ALERT_WEBHOOK_URL:
        channels.append(("webhook", lambda: send_webhook(payload)))
    if SMTP_HOST and ALERT_EMAIL_TO:
        subject = f"[SAP Basis Sentinel] {result.get('status', 'alert').upper()}: {target['name']}"
        body = "\\n".join(
            f"{key}: {value}" for key, value in payload.items()
            if key not in ("event", "occurred_at")
        )
        channels.append(("email", lambda subject=subject, body=body: send_email(subject, body)))
    return channels


def _send_once(target: dict, fingerprint: str, channels) -> None:
    for channel, sender in channels:
        event_fingerprint = f"{fingerprint}:{channel}"
        with get_db() as connection:
            already_sent = connection.execute(
                select(notification_events.c.id).where(
                    notification_events.c.target_id == target["id"],
                    notification_events.c.fingerprint == event_fingerprint,
                    notification_events.c.result == "sent",
                ).limit(1)
            ).first()
        if already_sent:
            continue
        ok, error = sender()
        with get_db() as connection:
            connection.execute(notification_events.insert().values(
                target_id=target["id"], fingerprint=event_fingerprint, channel=channel,
                result="sent" if ok else "failed", error=error,
            ))


def dispatch_alerts(target: dict, result: dict, last_healthy_check_id: int | None = None) -> None:
    channels = _notification_channels(target, result)
    if not channels:
        return

    if result.get("status") == "unreachable":
        # A new fingerprint is created after every successful check, so a later outage alerts again.
        outage_anchor = last_healthy_check_id or 0
        _send_once(target, f"unreachable:{outage_anchor}", channels)
        return

    days = result.get("days_remaining")
    if days is None:
        return
    thresholds = [-1] if days < 0 else [
        threshold for threshold in ALERT_THRESHOLDS_DAYS if 0 <= days <= threshold
    ]
    for threshold in thresholds:
        label = "expired" if threshold == -1 else f"{threshold}d"
        fingerprint = f"{result.get('serial_number')}:{label}"
        _send_once(target, fingerprint, channels)
