from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, MetaData, String,
    Table, Text, create_engine, inspect, text, true,
)
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.schema import CreateTable

from .config import DATABASE_URL

metadata = MetaData()
targets = Table(
    "targets", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(120), nullable=False),
    Column("hostname", String(253), nullable=False),
    Column("port", Integer, nullable=False, default=443),
    Column("enabled", Boolean, nullable=False, default=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("source", String(30), nullable=False, server_default="endpoint"),
    Column("sap_system_id", String(3)),
    Column("sap_client", String(3)),
    Column("pse_name", String(255)),
    Column("system_id", Integer, ForeignKey("sap_systems.id", ondelete="SET NULL")),
)
Index("uq_targets_hostname_port", targets.c.hostname, targets.c.port, unique=True)

certificate_checks = Table(
    "certificate_checks", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("target_id", Integer, ForeignKey("targets.id", ondelete="CASCADE"), nullable=False),
    Column("checked_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("subject", Text),
    Column("issuer", Text),
    Column("serial_number", String(128)),
    Column("not_before", String(40)),
    Column("not_after", String(40)),
    Column("days_remaining", Integer),
    Column("tls_verified", Boolean, nullable=False, default=False),
    Column("status", String(30), nullable=False),
    Column("error", Text),
)
notification_events = Table(
    "notification_events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("target_id", Integer, ForeignKey("targets.id", ondelete="CASCADE"), nullable=False),
    Column("fingerprint", String(300), nullable=False),
    Column("channel", String(30), nullable=False),
    Column("sent_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("result", String(30), nullable=False),
    Column("error", Text),
)

# Registry of managed SAP systems (ECC, S/4HANA, NetWeaver ABAP/Java, BW, PI/PO, ...).
sap_systems = Table(
    "sap_systems", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("sid", String(8), nullable=False),
    Column("name", String(120), nullable=False),
    Column("product", String(30), nullable=False),
    Column("product_version", String(60)),
    Column("basis_release", String(20)),
    Column("stack", String(10), nullable=False),
    Column("database", String(10), nullable=False),
    Column("environment", String(10), nullable=False),
    Column("landscape", String(80), nullable=False, server_default="default"),
    Column("license_model", String(20)),
    Column("host", String(253)),
    Column("default_client", String(3)),
    Column("description", Text),
    Column("timezone", String(64)),
    Column("control_enabled", Boolean),   # NULL = default (off for PRD, on otherwise)
    # Comma-separated subset of application,database,os. NULL = every layer (full monitoring).
    Column("monitoring_layers", String(40)),
    Column("enabled", Boolean, nullable=False, default=True, server_default=true()),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)
Index("uq_sap_systems_sid_landscape", sap_systems.c.sid, sap_systems.c.landscape, unique=True)

SYSTEM_FK = lambda nullable=False, ondelete="CASCADE": Column(  # noqa: E731
    "system_id", Integer, ForeignKey("sap_systems.id", ondelete=ondelete), nullable=nullable)

# Generic, module-agnostic alert de-duplication (growth, transports, imported certs).
alert_events = Table(
    "alert_events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    SYSTEM_FK(nullable=True),
    Column("module", String(40), nullable=False),
    Column("fingerprint", String(300), nullable=False),
    Column("channel", String(30), nullable=False),
    Column("severity", String(20), nullable=False),
    Column("summary", Text, nullable=False),
    Column("sent_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("result", String(30), nullable=False),
    Column("error", Text),
)
Index("ix_alert_events_fingerprint", alert_events.c.fingerprint, alert_events.c.channel)

# Certificates exported from STRUST/PSEs or Java key storage (public certificates only, never keys).
imported_certificates = Table(
    "imported_certificates", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    SYSTEM_FK(),
    Column("pse_name", String(255), nullable=False),
    Column("subject", Text, nullable=False),
    Column("issuer", Text, nullable=False),
    Column("serial_number", String(128), nullable=False),
    Column("not_before", String(40), nullable=False),
    Column("not_after", String(40), nullable=False),
    Column("sha256_fingerprint", String(64), nullable=False),
    Column("imported_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)
Index("uq_imported_cert", imported_certificates.c.system_id, imported_certificates.c.pse_name,
      imported_certificates.c.sha256_fingerprint, unique=True)

# Table growth watchdog snapshots.
table_size_snapshots = Table(
    "table_size_snapshots", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    SYSTEM_FK(),
    Column("table_name", String(128), nullable=False),
    Column("size_mb", Float, nullable=False),
    Column("row_count", BigInteger),
    Column("captured_at", DateTime(timezone=True), nullable=False),
)
Index("ix_table_size_sys_tab", table_size_snapshots.c.system_id, table_size_snapshots.c.table_name,
      table_size_snapshots.c.captured_at)

capacity_snapshots = Table(
    "capacity_snapshots", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    SYSTEM_FK(),
    Column("disk_total_mb", Float, nullable=False),
    Column("disk_used_mb", Float, nullable=False),
    Column("memory_total_mb", Float),
    Column("memory_used_mb", Float),
    Column("captured_at", DateTime(timezone=True), nullable=False),
)
Index("ix_capacity_sys", capacity_snapshots.c.system_id, capacity_snapshots.c.captured_at)

# Summary-only history of stateless analyses (no user names or uploaded content).
analysis_runs = Table(
    "analysis_runs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    SYSTEM_FK(),
    Column("module", String(40), nullable=False),
    Column("status", String(20), nullable=False),
    Column("summary", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)
Index("ix_analysis_runs_sys", analysis_runs.c.system_id, analysis_runs.c.module, analysis_runs.c.created_at)

# Transport routes between systems (STMS/CTS+ consolidation and delivery routes).
transport_routes = Table(
    "transport_routes", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source_system_id", Integer, ForeignKey("sap_systems.id", ondelete="CASCADE"), nullable=False),
    Column("target_system_id", Integer, ForeignKey("sap_systems.id", ondelete="CASCADE"), nullable=False),
    Column("route_type", String(20), nullable=False),
    Column("transport_layer", String(20)),
    Column("description", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)
Index("uq_transport_route", transport_routes.c.source_system_id, transport_routes.c.target_system_id, unique=True)

# Import history per target system (STMS import history / TPALOG / post-check uploads).
transport_imports = Table(
    "transport_imports", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    SYSTEM_FK(),
    Column("trkorr", String(20), nullable=False),
    Column("rc", Integer, nullable=False),
    Column("result", String(10), nullable=False),
    Column("imported_at", DateTime(timezone=True), nullable=False),
    Column("time_known", Boolean, nullable=False, default=True),
    Column("owner", String(40)),
    Column("description", Text),
    Column("uploaded_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)
Index("ix_transport_imports_sys", transport_imports.c.system_id, transport_imports.c.imported_at)

# Background job runs (SM37 / TBTCO exports). Upserted per job so status changes are tracked.
job_runs = Table(
    "job_runs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    SYSTEM_FK(),
    Column("job_name", String(64), nullable=False),
    Column("job_count", String(20)),
    Column("status", String(12), nullable=False),
    Column("scheduled_by", String(40)),
    Column("start_at", DateTime(timezone=True)),
    Column("end_at", DateTime(timezone=True)),
    Column("duration_s", Integer),
    Column("captured_at", DateTime(timezone=True), nullable=False),
)
Index("ix_job_runs_sys", job_runs.c.system_id, job_runs.c.start_at)
Index("ix_job_runs_key", job_runs.c.system_id, job_runs.c.job_name, job_runs.c.job_count)

# SAP enqueue lock snapshots (SM12 exports).
lock_snapshots = Table(
    "lock_snapshots", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    SYSTEM_FK(),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    Column("total", Integer, nullable=False),
)
Index("ix_lock_snapshots_sys", lock_snapshots.c.system_id, lock_snapshots.c.captured_at)

lock_entries = Table(
    "lock_entries", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("snapshot_id", Integer, ForeignKey("lock_snapshots.id", ondelete="CASCADE"), nullable=False),
    Column("client", String(3)),
    Column("user_name", String(40), nullable=False),
    Column("lock_mode", String(2)),
    Column("table_name", String(64), nullable=False),
    Column("argument", Text),
    Column("locked_at", DateTime(timezone=True), nullable=False),
    Column("age_minutes", Integer, nullable=False),
)
Index("ix_lock_entries_snapshot", lock_entries.c.snapshot_id)

# ---------------------------------------------------------------- platform: users & audit
users = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("username", String(64), nullable=False),
    Column("display_name", String(120)),
    Column("email", String(254)),
    Column("password_hash", String(300), nullable=False),
    Column("role", String(20), nullable=False),
    Column("active", Boolean, nullable=False, default=True, server_default=true()),
    Column("token_version", Integer, nullable=False, default=1, server_default=text("1")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("last_login_at", DateTime(timezone=True)),
)
Index("uq_users_username", users.c.username, unique=True)

audit_log = Table(
    "audit_log", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("username", String(64), nullable=False),
    Column("action", String(80), nullable=False),
    Column("target", String(300)),
    Column("status", Integer),
    Column("detail", Text),
    Column("ip", String(64)),
)
Index("ix_audit_log_at", audit_log.c.at)

# ---------------------------------------------------------------- live collectors & monitors
collectors = Table(
    "collectors", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    SYSTEM_FK(),
    Column("type", String(20), nullable=False),          # sapcontrol | hana
    Column("host", String(253), nullable=False),
    Column("port", Integer, nullable=False),
    Column("use_tls", Boolean, nullable=False, default=True),
    Column("verify_tls", Boolean, nullable=False, default=True),
    Column("username", String(120)),
    Column("password_enc", Text),
    Column("database_name", String(64)),                # HANA tenant (informational / SYSTEMDB routing)
    Column("abap_schema", String(64)),                  # e.g. SAPHANADB / SAPABAP1: enables ABAP table monitors
    Column("private_key_enc", Text),                     # os_ssh: encrypted private key (alternative to password)
    Column("host_key", String(120)),                     # os_ssh: pinned SSH host key fingerprint (TOFU)
    Column("sap_client", String(3)),                     # rfc: logon client
    Column("interval_seconds", Integer, nullable=False, default=300),
    Column("enabled", Boolean, nullable=False, default=True),
    Column("last_run_at", DateTime(timezone=True)),
    Column("last_status", String(20)),
    Column("last_error", Text),
    Column("last_duration_ms", Integer),
    Column("last_details", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)
Index("ix_collectors_system", collectors.c.system_id)

monitor_snapshots = Table(
    "monitor_snapshots", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    SYSTEM_FK(),
    Column("monitor", String(40), nullable=False),
    Column("source", String(20), nullable=False),        # sapcontrol | hana | upload | simulator
    Column("captured_at", DateTime(timezone=True), nullable=False),
    Column("health", String(20), nullable=False),
    Column("data", Text, nullable=False),
)
Index("ix_monitor_snapshots", monitor_snapshots.c.system_id, monitor_snapshots.c.monitor, monitor_snapshots.c.captured_at)

# Latest value of every metric per system (upserted on each write): fleet views and daily checks read this instead of
# scanning the time series.
metric_latest = Table(
    "metric_latest", metadata,
    SYSTEM_FK(),
    Column("metric", String(80), nullable=False),
    Column("value", Float, nullable=False),
    Column("ts", DateTime(timezone=True), nullable=False),
)
Index("uq_metric_latest", metric_latest.c.system_id, metric_latest.c.metric, unique=True)

# Daily Basis check sign-off: who reviewed the fleet (or a landscape) when, with the check results at that moment.
daily_check_reviews = Table(
    "daily_check_reviews", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("check_date", String(10), nullable=False),     # YYYY-MM-DD (reviewer's day)
    Column("landscape", String(80)),                       # null = whole fleet
    Column("reviewed_by", String(80), nullable=False),
    Column("reviewed_at", DateTime(timezone=True), nullable=False),
    Column("comment", Text),
    Column("summary", Text, nullable=False),               # JSON: counts per status
    Column("results", Text, nullable=False),               # JSON: the matrix as reviewed
)
Index("ix_daily_check_reviews", daily_check_reviews.c.check_date, daily_check_reviews.c.landscape)

# ABAP push agent (report Z_SENTINEL_PUSH in the SAP system): one token per system; only its SHA-256 is stored.
agent_tokens = Table(
    "agent_tokens", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    SYSTEM_FK(),
    Column("token_hash", String(64), nullable=False),
    Column("hint", String(12), nullable=False),          # first characters, to recognise the token in the SAP variant
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("created_by", String(80)),
    Column("revoked_at", DateTime(timezone=True)),
    Column("last_seen_at", DateTime(timezone=True)),
    Column("last_ip", String(64)),
    Column("last_agent_version", String(20)),
    Column("last_payload_bytes", Integer),
)
Index("uq_agent_tokens_hash", agent_tokens.c.token_hash, unique=True)
Index("ix_agent_tokens_system", agent_tokens.c.system_id)

# Pointer to the newest snapshot of each (system, monitor): dashboards read the current state of 50+ systems without
# scanning snapshot history.
monitor_latest = Table(
    "monitor_latest", metadata,
    SYSTEM_FK(),
    Column("monitor", String(40), nullable=False),
    Column("snapshot_id", Integer, nullable=False),
)
Index("uq_monitor_latest", monitor_latest.c.system_id, monitor_latest.c.monitor, unique=True)

metric_samples = Table(
    "metric_samples", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    SYSTEM_FK(),
    Column("metric", String(80), nullable=False),
    Column("value", Float, nullable=False),
    Column("ts", DateTime(timezone=True), nullable=False),
)
Index("ix_metric_samples", metric_samples.c.system_id, metric_samples.c.metric, metric_samples.c.ts)

# High-volume metric stream. TimescaleDB turns this into a hypertable in PostgreSQL
# deployments; the same table remains an ordinary indexed table for SQLite/dev fallback.
# No synthetic row id is needed: time-series rows are append-only observations.
metric_points = Table(
    "metric_points", metadata,
    SYSTEM_FK(),
    Column("metric", String(80), nullable=False),
    Column("value", Float, nullable=False),
    Column("ts", DateTime(timezone=True), nullable=False),
)
Index("ix_metric_points_lookup", metric_points.c.system_id, metric_points.c.metric, metric_points.c.ts)
Index("ix_metric_points_time", metric_points.c.ts)

# ---------------------------------------------------------------- incidents
alert_rules = Table(
    "alert_rules", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(120), nullable=False),
    Column("metric", String(80), nullable=False),
    Column("operator", String(2), nullable=False),         # >, >=, <, <=
    Column("warning", Float),
    Column("critical", Float),
    Column("scope_type", String(20), nullable=False, server_default="global"),   # global | landscape | system
    Column("scope_value", String(80)),
    Column("enabled", Boolean, nullable=False, default=True),
    Column("description", Text),
    Column("builtin", Boolean, nullable=False, default=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

incidents = Table(
    "incidents", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("system_id", Integer, ForeignKey("sap_systems.id", ondelete="CASCADE")),
    Column("fingerprint", String(300), nullable=False),
    Column("source", String(20), nullable=False),          # rule | module
    Column("metric", String(80)),
    Column("title", Text, nullable=False),
    Column("severity", String(20), nullable=False),
    Column("status", String(20), nullable=False),          # open | acknowledged | resolved
    Column("details", Text),
    Column("value", Float),
    Column("opened_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
    Column("occurrences", Integer, nullable=False, default=1),
    Column("assignee", String(64)),
    Column("acknowledged_by", String(64)),
    Column("acknowledged_at", DateTime(timezone=True)),
    Column("resolved_by", String(64)),
    Column("resolved_at", DateTime(timezone=True)),
    Column("resolution", Text),
    Column("in_maintenance", Boolean, nullable=False, default=False),
)
Index("ix_incidents_status", incidents.c.status, incidents.c.system_id)
Index("ix_incidents_fingerprint", incidents.c.fingerprint, incidents.c.status)

incident_events = Table(
    "incident_events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("incident_id", Integer, ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False),
    Column("at", DateTime(timezone=True), nullable=False),
    Column("actor", String(64), nullable=False),
    Column("kind", String(30), nullable=False),
    Column("message", Text),
)
Index("ix_incident_events", incident_events.c.incident_id, incident_events.c.at)

maintenance_windows = Table(
    "maintenance_windows", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("scope_type", String(20), nullable=False),      # global | landscape | system
    Column("scope_value", String(80)),
    Column("starts_at", DateTime(timezone=True), nullable=False),
    Column("ends_at", DateTime(timezone=True), nullable=False),
    Column("reason", Text, nullable=False),
    Column("created_by", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)
Index("ix_maintenance_window", maintenance_windows.c.starts_at, maintenance_windows.c.ends_at)

# ---------------------------------------------------------------- discovery & lifecycle control
sap_instances = Table(
    "sap_instances", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    SYSTEM_FK(),
    Column("hostname", String(253), nullable=False),
    Column("instance_nr", String(2), nullable=False),
    Column("instance_name", String(40)),            # ASCS01, ERS02, D00, DVEBMGS00, J00, HDB00, W00 ...
    Column("role", String(20), nullable=False),     # ASCS | SCS | ERS | PAS | AAS | JAVA | HANA | WEBDISP | SMDA | GATEWAY | OTHER
    Column("role_source", String(20), nullable=False, server_default="features"),  # instance_name | features | inferred | manual
    Column("features", String(200)),
    Column("http_port", Integer),
    Column("https_port", Integer),
    Column("start_priority", String(10)),
    Column("status", String(10)),                   # green | yellow | red | gray
    Column("last_seen_at", DateTime(timezone=True)),
)
Index("uq_sap_instances", sap_instances.c.system_id, sap_instances.c.hostname, sap_instances.c.instance_nr, unique=True)

system_operations = Table(
    "system_operations", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    SYSTEM_FK(),
    Column("action", String(10), nullable=False),        # start | stop | restart
    Column("scope", String(10), nullable=False),         # system (SAP + DB) | app | db | instance
    Column("options", String(40)),                       # system scope: ALL | ABAP | J2EE | SCS | DIALOG ...
    Column("hostname", String(253)),
    Column("instance_nr", String(2)),
    Column("soft_timeout", Integer),
    Column("reason", Text, nullable=False),
    Column("requested_by", String(64), nullable=False),
    Column("status", String(12), nullable=False),        # running | succeeded | failed | timeout | interrupted
    Column("message", Text),
    Column("progress", Text),                            # JSON list of {at, instances:{name: status}}
    Column("maintenance_window_id", Integer),
    Column("steps", Text),                               # JSON procedure steps (checks, manual confirmations, actions)
    Column("checklist", Text),                           # JSON manual confirmations given with the request
    Column("force", Boolean),                            # proceed despite blocking pre-checks
    Column("abort_requested", Boolean),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
)
Index("ix_system_operations", system_operations.c.system_id, system_operations.c.started_at)

# Configuration & Security Analysis: immutable configuration snapshots and drift baseline.
configuration_snapshots = Table(
    "configuration_snapshots", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    SYSTEM_FK(),
    Column("source", String(40), nullable=False),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    Column("is_baseline", Boolean, nullable=False, default=False, server_default=text("false")),
    Column("snapshot_json", Text, nullable=False),
    Column("change_count", Integer, nullable=False, default=0, server_default=text("0")),
)
Index("ix_configuration_snapshots_sys", configuration_snapshots.c.system_id,
      configuration_snapshots.c.captured_at)

# Explicit infrastructure and application dependency inventory.
# Assets are landscape-scoped; system_id is optional for shared infrastructure.
topology_assets = Table(
    "topology_assets", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("landscape", String(80), nullable=False),
    Column("system_id", Integer, ForeignKey("sap_systems.id", ondelete="SET NULL")),
    Column("kind", String(30), nullable=False),  # host | hana | rfc_destination | middleware | web_dispatcher | external_service
    Column("name", String(160), nullable=False),
    Column("hostname", String(253)),
    Column("port", Integer),
    Column("attributes_json", Text, nullable=False, server_default="{}"),
    Column("source", String(20), nullable=False, server_default="manual"),  # manual | sapcontrol | hana
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)
Index("ix_topology_assets_landscape", topology_assets.c.landscape, topology_assets.c.kind)
Index("ix_topology_assets_system", topology_assets.c.system_id)

topology_links = Table(
    "topology_links", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source_asset_id", Integer, ForeignKey("topology_assets.id", ondelete="CASCADE"), nullable=False),
    Column("target_asset_id", Integer, ForeignKey("topology_assets.id", ondelete="CASCADE"), nullable=False),
    Column("relationship", String(30), nullable=False),  # runs_on | uses_database | connects_to | routes_to | depends_on | hosts
    Column("source", String(20), nullable=False, server_default="manual"),
    Column("verified_at", DateTime(timezone=True)),
    Column("notes", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)
Index("uq_topology_link", topology_links.c.source_asset_id, topology_links.c.target_asset_id,
      topology_links.c.relationship, unique=True)
Index("ix_topology_links_target", topology_links.c.target_asset_id)


# Columns added to pre-existing tables (applied by init_db and Alembic).
ADDED_COLUMNS = {
    "targets": [("system_id", "INTEGER REFERENCES sap_systems(id) ON DELETE SET NULL")],
    "sap_systems": [("timezone", "VARCHAR(64)"), ("control_enabled", "BOOLEAN"), ("monitoring_layers", "VARCHAR(40)")],
    "collectors": [("private_key_enc", "TEXT"), ("host_key", "VARCHAR(120)"), ("sap_client", "VARCHAR(3)")],
    "system_operations": [("steps", "TEXT"), ("checklist", "TEXT"), ("force", "BOOLEAN"), ("abort_requested", "BOOLEAN")],
}

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        if DATABASE_URL.startswith("sqlite:///"):
            db_path = DATABASE_URL.removeprefix("sqlite:///")
            if db_path != ":memory:":
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        from . import config

        pool = {} if DATABASE_URL.startswith("sqlite") else {
            "pool_size": config.DB_POOL_SIZE, "max_overflow": config.DB_MAX_OVERFLOW,
            "pool_timeout": config.DB_POOL_TIMEOUT_SECONDS, "pool_recycle": 1800}
        _engine = create_engine(
            DATABASE_URL,
            future=True,
            pool_pre_ping=True,
            connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
            **pool,
        )
        if DATABASE_URL.startswith("sqlite"):
            from sqlalchemy import event

            @event.listens_for(_engine, "connect")
            def enable_sqlite_foreign_keys(dbapi_connection, _record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()
    return _engine


@contextmanager
def get_db():
    with get_engine().begin() as connection:
        yield connection


def _has_target_unique_index(connection: Connection) -> bool:
    inspector = inspect(connection)
    if "targets" not in inspector.get_table_names():
        return False
    index_names = {item["name"] for item in inspector.get_indexes("targets")}
    constraint_names = {
        item.get("name") for item in inspector.get_unique_constraints("targets")
    }
    return (
        "uq_targets_hostname_port" in index_names
        or "uq_targets_hostname_port" in constraint_names
    )


def assert_no_duplicate_targets(connection: Connection) -> None:
    """Preflight legacy data before metadata.create_all attempts the unique index."""
    if "targets" not in inspect(connection).get_table_names():
        return
    if _has_target_unique_index(connection):
        return
    duplicates = connection.execute(
        text(
            "SELECT hostname, port, COUNT(*) AS duplicate_count "
            "FROM targets GROUP BY hostname, port HAVING COUNT(*) > 1 LIMIT 10"
        )
    ).mappings().all()
    if duplicates:
        examples = ", ".join(
            f"{row['hostname']}:{row['port']} ({row['duplicate_count']} rows)"
            for row in duplicates
        )
        raise RuntimeError(
            "Cannot enforce unique certificate targets: duplicate hostname/port "
            f"records exist. Resolve these records and retry: {examples}"
        )


def ensure_target_uniqueness(connection: Connection) -> None:
    if _has_target_unique_index(connection):
        return
    assert_no_duplicate_targets(connection)
    Index(
        "uq_targets_hostname_port", targets.c.hostname, targets.c.port, unique=True
    ).create(bind=connection, checkfirst=True)


def init_db() -> None:
    engine = get_engine()
    with engine.begin() as connection:
        assert_no_duplicate_targets(connection)
        existing_tables = set(inspect(connection).get_table_names())
        missing_tables = [
            table for table in metadata.sorted_tables if table.name not in existing_tables
        ]
        # Execute table DDL directly: Table.create also traverses FK dependencies
        # and can attempt to recreate indexes on already-existing parent tables.
        create_missing_tables(connection, missing_tables)
        if any(t.name == "monitor_latest" for t in missing_tables):
            backfill_monitor_latest(connection)
        if any(t.name == "metric_latest" for t in missing_tables):
            backfill_metric_latest(connection)
        add_missing_columns(connection)
        ensure_target_uniqueness(connection)


def backfill_monitor_latest(connection: Connection) -> None:
    connection.execute(text(
        "INSERT INTO monitor_latest (system_id, monitor, snapshot_id) "
        "SELECT system_id, monitor, MAX(id) FROM monitor_snapshots GROUP BY system_id, monitor"))


def backfill_metric_latest(connection: Connection) -> None:
    """Latest point per (system, metric) from the last 7 days; other metrics fill on their next collection."""
    since = datetime.now(timezone.utc) - timedelta(days=7)
    connection.execute(text(
        "INSERT INTO metric_latest (system_id, metric, value, ts) "
        "SELECT p.system_id, p.metric, MAX(p.value), p.ts FROM metric_points p "
        "JOIN (SELECT system_id, metric, MAX(ts) AS ts FROM metric_points WHERE ts >= :since GROUP BY system_id, metric) m "
        "ON m.system_id = p.system_id AND m.metric = p.metric AND m.ts = p.ts "
        "GROUP BY p.system_id, p.metric, p.ts"), {"since": since})


def add_missing_columns(connection: Connection) -> None:
    """Additive, idempotent column upgrades for tables created by earlier releases."""
    inspector = inspect(connection)
    for table_name, columns in ADDED_COLUMNS.items():
        if table_name not in inspector.get_table_names():
            continue
        existing = {c["name"] for c in inspector.get_columns(table_name)}
        for name, ddl in columns:
            if name not in existing:
                connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {name} {ddl}"))


def create_missing_tables(connection: Connection, missing_tables) -> None:
    """Create new tables and their own indexes. Existing tables are never altered."""
    for table in missing_tables:
        connection.execute(CreateTable(table))
        for index in table.indexes:
            if table.name == "targets":
                continue  # handled by ensure_target_uniqueness (duplicate preflight)
            index.create(bind=connection, checkfirst=True)


def reset_engine_for_tests() -> None:
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None
