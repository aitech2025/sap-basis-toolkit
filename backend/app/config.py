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

# Mandatory allowlist. Supports exact hosts and wildcard subdomains.
ALLOWED_TARGET_HOSTS = tuple(
    item.strip().lower().rstrip(".")
    for item in os.getenv("ALLOWED_TARGET_HOSTS", "").split(",")
    if item.strip()
)
# Private SAP network ranges must be explicitly allowed.
ALLOWED_TARGET_CIDRS = tuple(
    item.strip() for item in os.getenv("ALLOWED_TARGET_CIDRS", "").split(",") if item.strip()
)
# Only these TCP destination ports may be probed on approved hosts.
ALLOWED_TARGET_PORTS = tuple(
    int(item.strip()) for item in os.getenv("ALLOWED_TARGET_PORTS", "443").split(",") if item.strip()
)

# --- Alert channel formatting -------------------------------------------------
# generic = raw JSON payload, slack = Slack incoming webhook, teams = Teams Workflows (Adaptive Card)
ALERT_WEBHOOK_FORMAT = os.getenv("ALERT_WEBHOOK_FORMAT", "generic").strip().lower()
PAGERDUTY_ROUTING_KEY = os.getenv("PAGERDUTY_ROUTING_KEY", "").strip()
PAGERDUTY_EVENTS_URL = os.getenv("PAGERDUTY_EVENTS_URL", "https://events.pagerduty.com/v2/enqueue")

# --- Upload limits for offline analyzers --------------------------------------
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))

# --- Table growth watchdog ------------------------------------------------------
GROWTH_ALERT_DAYS = int(os.getenv("GROWTH_ALERT_DAYS", "30"))
GROWTH_CAPACITY_THRESHOLD_PCT = float(os.getenv("GROWTH_CAPACITY_THRESHOLD_PCT", "90"))
GROWTH_EVAL_INTERVAL_MINUTES = int(os.getenv("GROWTH_EVAL_INTERVAL_MINUTES", "1440"))

# --- Inactive user & license optimizer ----------------------------------------
DORMANT_USER_DAYS = int(os.getenv("DORMANT_USER_DAYS", "90"))
PROTECTED_USERS = tuple(
    v.strip().upper()
    for v in os.getenv("PROTECTED_USERS", "SAP*,DDIC,TMSADM,EARLYWATCH,SAPCPIC").split(",")
    if v.strip()
)

# --- Transport checker ----------------------------------------------------------
# Tables whose content must never be transported without explicit approval.
PROTECTED_TABLES = tuple(
    v.strip().upper()
    for v in os.getenv(
        "PROTECTED_TABLES",
        "T000,USR01,USR02,USR04,UST04,AGR_USERS,RFCDES,TBTCO,TBTCP,TVARVC,PRGN_CUST,SSM_CUST",
    ).split(",")
    if v.strip()
)
