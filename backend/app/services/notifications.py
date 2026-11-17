import json
import smtplib
from email.message import EmailMessage
from urllib.request import Request, urlopen
from ..config import ALERT_EMAIL_TO, ALERT_WEBHOOK_URL, SMTP_FROM, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_STARTTLS, SMTP_USERNAME

def send_webhook(payload: dict) -> tuple[bool, str | None]:
    if not ALERT_WEBHOOK_URL: return False, "Webhook not configured"
    request = Request(ALERT_WEBHOOK_URL, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=10) as response:
            if response.status >= 300: return False, f"Webhook returned HTTP {response.status}"
        return True, None
    except Exception as exc: return False, str(exc)[:1000]

def send_email(subject: str, body: str) -> tuple[bool, str | None]:
    if not SMTP_HOST or not ALERT_EMAIL_TO or not SMTP_FROM: return False, "SMTP or recipient configuration is incomplete"
    message = EmailMessage(); message["Subject"] = subject; message["From"] = SMTP_FROM; message["To"] = ", ".join(ALERT_EMAIL_TO); message.set_content(body)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as client:
            if SMTP_STARTTLS: client.starttls()
            if SMTP_USERNAME: client.login(SMTP_USERNAME, SMTP_PASSWORD)
            client.send_message(message)
        return True, None
    except Exception as exc: return False, str(exc)[:1000]

def notify_certificate(payload: dict) -> list[dict]:
    outcomes = []
    if ALERT_WEBHOOK_URL:
        ok, error = send_webhook(payload); outcomes.append({"channel": "webhook", "success": ok, "error": error})
    if SMTP_HOST and ALERT_EMAIL_TO:
        subject = f"[SAP Basis Sentinel] {payload['status'].upper()}: {payload['target_name']}"
        body = "\n".join([f"Target: {payload['target_name']} ({payload['hostname']}:{payload['port']})", f"Status: {payload['status']}", f"Days remaining: {payload.get('days_remaining')}", f"Certificate expires: {payload.get('not_after')}", f"Subject: {payload.get('subject')}"])
        ok, error = send_email(subject, body); outcomes.append({"channel": "email", "success": ok, "error": error})
    return outcomes
