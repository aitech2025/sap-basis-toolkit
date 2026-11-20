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

# --- Multi-system platform -----------------------------------------------------
PROFILE_OVERRIDES_FILE = os.getenv("PROFILE_OVERRIDES_FILE", "").strip()
# Built React UI (vite build output). Served at / when present.
FRONTEND_DIST = os.getenv("FRONTEND_DIST", os.path.join(os.path.dirname(__file__), "static"))

# --- Operations monitoring (jobs, locks, transport history) ---------------------
JOB_LONG_RUNNING_MINUTES = int(os.getenv("JOB_LONG_RUNNING_MINUTES", "120"))
LOCK_WARNING_MINUTES = int(os.getenv("LOCK_WARNING_MINUTES", "60"))
# Live SM12 (sapcontrol EnqGetLockTable): persist a lock snapshot at most this often, keeping locks held at least N minutes.
LOCK_SNAPSHOT_SECONDS = int(os.getenv("LOCK_SNAPSHOT_SECONDS", "300"))
LOCK_STORE_MIN_AGE_MINUTES = int(os.getenv("LOCK_STORE_MIN_AGE_MINUTES", "5"))
LOCK_CRITICAL_MINUTES = int(os.getenv("LOCK_CRITICAL_MINUTES", "240"))
OPS_DATA_STALE_HOURS = int(os.getenv("OPS_DATA_STALE_HOURS", "24"))
OPS_RETENTION_DAYS = int(os.getenv("OPS_RETENTION_DAYS", "90"))

# --- Platform: authentication -------------------------------------------------------------
# Signs session tokens and encrypts stored collector credentials. Falls back to a key derived
# from API_AUTH_TOKEN so existing deployments keep working, but set it explicitly in production.
SECRET_KEY = os.getenv("SECRET_KEY", "").strip()
SESSION_HOURS = int(os.getenv("SESSION_HOURS", "12"))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
LOGIN_MAX_FAILURES = int(os.getenv("LOGIN_MAX_FAILURES", "5"))
AUDIT_RETENTION_DAYS = int(os.getenv("AUDIT_RETENTION_DAYS", "365"))

# --- Live collectors ----------------------------------------------------------------------
# ABAP push agent (report Z_SENTINEL_PUSH)
ABAP_SOURCES_DIR = os.getenv("ABAP_SOURCES_DIR", "")
AGENT_MAX_BYTES = int(os.getenv("AGENT_MAX_BYTES", str(8 * 1024 * 1024)))
AGENT_MIN_INTERVAL_SECONDS = int(os.getenv("AGENT_MIN_INTERVAL_SECONDS", "20"))
AGENT_SILENT_SECONDS = int(os.getenv("AGENT_SILENT_SECONDS", "900"))
INTELLIGENCE_CACHE_SECONDS = int(os.getenv("INTELLIGENCE_CACHE_SECONDS", "60"))
COLLECTOR_TICK_SECONDS = int(os.getenv("COLLECTOR_TICK_SECONDS", "15"))
COLLECTOR_TIMEOUT_SECONDS = float(os.getenv("COLLECTOR_TIMEOUT_SECONDS", "15"))
COLLECTOR_MAX_PARALLEL = int(os.getenv("COLLECTOR_MAX_PARALLEL", "16"))
# Per-instance sapcontrol calls inside one collector run (systems with many application servers).
SAPCONTROL_INTERVAL_SECONDS = int(os.getenv("SAPCONTROL_INTERVAL_SECONDS", "60"))
SAPCONTROL_INSTANCE_PARALLEL = int(os.getenv("SAPCONTROL_INSTANCE_PARALLEL", "4"))
CERT_CHECK_PARALLEL = int(os.getenv("CERT_CHECK_PARALLEL", "8"))
# SQLAlchemy pool per process. The worker needs at least COLLECTOR_MAX_PARALLEL connections.
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "10"))
DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "20"))
DB_POOL_TIMEOUT_SECONDS = int(os.getenv("DB_POOL_TIMEOUT_SECONDS", "30"))
WP_LONG_RUNNING_SECONDS = int(os.getenv("WP_LONG_RUNNING_SECONDS", "1800"))
METRIC_RETENTION_DAYS = int(os.getenv("METRIC_RETENTION_DAYS", "30"))
SNAPSHOT_RETENTION_DAYS = int(os.getenv("SNAPSHOT_RETENTION_DAYS", "7"))
# Snapshot history older than this is thinned to one per hour (plus every health change).
SNAPSHOT_FULL_RESOLUTION_HOURS = int(os.getenv("SNAPSHOT_FULL_RESOLUTION_HOURS", "24"))
HANA_GROWTH_SNAPSHOT_HOURS = int(os.getenv("HANA_GROWTH_SNAPSHOT_HOURS", "24"))
# Optional JSON file overriding/adding collector SQL queries (see docs/monitoring.md).
QUERY_OVERRIDES_FILE = os.getenv("QUERY_OVERRIDES_FILE", "").strip()
# Demo mode: collectors with host "simulator" produce synthetic data. Never enable in production.
SIMULATOR_ENABLED = os.getenv("SIMULATOR_ENABLED", "false").lower() == "true"

# --- Discovery & lifecycle control --------------------------------------------------
DISCOVERY_PROBE_TIMEOUT_SECONDS = float(os.getenv("DISCOVERY_PROBE_TIMEOUT_SECONDS", "0.8"))
DISCOVERY_MAX_PARALLEL = int(os.getenv("DISCOVERY_MAX_PARALLEL", "48"))
# Global switch for start/stop/restart from Sentinel (per-system switch also required).
CONTROL_ENABLED = os.getenv("CONTROL_ENABLED", "true").lower() == "true"
CONTROL_TIMEOUT_SECONDS = int(os.getenv("CONTROL_TIMEOUT_SECONDS", "1200"))
CONTROL_POLL_SECONDS = float(os.getenv("CONTROL_POLL_SECONDS", "5"))
HANA_START_TIMEOUT_SECONDS = int(os.getenv("HANA_START_TIMEOUT_SECONDS", "3600"))
HANA_SQL_WAIT_SECONDS = int(os.getenv("HANA_SQL_WAIT_SECONDS", "300"))
# OS discovery and inventory over SSH (root or <sid>adm). Only fixed read-only commands are run.
SSH_ALLOWED_PORTS = os.getenv("SSH_ALLOWED_PORTS", "22")
SSH_TIMEOUT_SECONDS = float(os.getenv("SSH_TIMEOUT_SECONDS", "15"))
