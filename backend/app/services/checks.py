from datetime import datetime, timezone
from sqlalchemy import select
from ..config import ALERT_EMAIL_TO, ALERT_THRESHOLDS_DAYS, ALERT_WEBHOOK_URL, SMTP_HOST
from ..db import certificate_checks, get_db, notification_events, targets
from .certificates import inspect_certificate
from .notifications import send_email, send_webhook

def check_target(target_id: int) -> dict:
    with get_db() as connection:
        target = connection.execute(select(targets).where(targets.c.id == target_id, targets.c.enabled.is_(True))).mappings().first()
    if target is None: raise ValueError("Enabled target not found")
    try: result = inspect_certificate(target["hostname"], target["port"])
    except Exception as exc:
        result = {"subject": None, "issuer": None, "serial_number": None, "not_before": None, "not_after": None, "days_remaining": None, "tls_verified": False, "status": "unreachable", "error": str(exc)[:1000]}
    with get_db() as connection:
        check = dict(connection.execute(certificate_checks.insert().values(target_id=target_id, subject=result["subject"], issuer=result["issuer"], serial_number=result["serial_number"], not_before=result["not_before"], not_after=result["not_after"], days_remaining=result["days_remaining"], tls_verified=result["tls_verified"], status=result["status"], error=result["error"]).returning(certificate_checks)).mappings().one())
    dispatch_alerts(dict(target), result)
    return check

def dispatch_alerts(target: dict, result: dict) -> None:
    days = result.get("days_remaining")
    if days is None: return
    thresholds = [-1] if days < 0 else [t for t in ALERT_THRESHOLDS_DAYS if 0 <= days <= t]
    for threshold in thresholds:
        label = "expired" if threshold == -1 else f"{threshold}d"
        fingerprint = f"{result.get('serial_number')}:{label}"
        payload = {"event": "certificate.expiry", "target_name": target["name"], "hostname": target["hostname"], "port": target["port"], "status": result["status"], "days_remaining": days, "not_after": result.get("not_after"), "subject": result.get("subject"), "alert_threshold": label, "occurred_at": datetime.now(timezone.utc).isoformat()}
        channels = []
        if ALERT_WEBHOOK_URL: channels.append(("webhook", lambda: send_webhook(payload)))
        if SMTP_HOST and ALERT_EMAIL_TO:
            subject = f"[SAP Basis Sentinel] {result['status'].upper()}: {target['name']}"
            body = "\n".join([f"Target: {target['name']} ({target['hostname']}:{target['port']})", f"Status: {result['status']}", f"Days remaining: {days}", f"Certificate expires: {result.get('not_after')}", f"Subject: {result.get('subject')}"])
            channels.append(("email", lambda subject=subject, body=body: send_email(subject, body)))
        for channel, sender in channels:
            event_fingerprint = f"{fingerprint}:{channel}"
            with get_db() as connection:
                already_sent = connection.execute(select(notification_events.c.id).where(notification_events.c.target_id == target["id"], notification_events.c.fingerprint == event_fingerprint, notification_events.c.result == "sent").limit(1)).first()
            if already_sent: continue
            ok, error = sender()
            with get_db() as connection:
                connection.execute(notification_events.insert().values(target_id=target["id"], fingerprint=event_fingerprint, channel=channel, result="sent" if ok else "failed", error=error))
