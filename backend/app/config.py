import os

APP_NAME = "SAP Basis Sentinel"
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/sentinel.db")
CERTIFICATE_TIMEOUT_SECONDS = float(os.getenv("CERTIFICATE_TIMEOUT_SECONDS", "8"))
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "360"))
ALERT_THRESHOLDS_DAYS = tuple(sorted({int(v.strip()) for v in os.getenv("ALERT_THRESHOLDS_DAYS", "90,60,30,14,7").split(",") if v.strip()}, reverse=True))
DEFAULT_WARNING_DAYS = int(os.getenv("DEFAULT_WARNING_DAYS", "30"))
ALERT_WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL", "")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")
ALERT_EMAIL_TO = tuple(v.strip() for v in os.getenv("ALERT_EMAIL_TO", "").split(",") if v.strip())
SMTP_STARTTLS = os.getenv("SMTP_STARTTLS", "true").lower() == "true"
API_AUTH_TOKEN = os.getenv("API_AUTH_TOKEN", "").strip()

# Only these TCP destination ports may be probed on approved hosts.
ALLOWED_TARGET_PORTS = tuple(
    int(item.strip()) for item in os.getenv("ALLOWED_TARGET_PORTS", "443").split(",") if item.strip()
)
