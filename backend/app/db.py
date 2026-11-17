from contextlib import contextmanager
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

# Columns added to pre-existing tables (applied by init_db and Alembic).
ADDED_COLUMNS = {
    "targets": [("system_id", "INTEGER REFERENCES sap_systems(id) ON DELETE SET NULL")],
}

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        if DATABASE_URL.startswith("sqlite:///"):
            db_path = DATABASE_URL.removeprefix("sqlite:///")
            if db_path != ":memory:":
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            DATABASE_URL,
            future=True,
            pool_pre_ping=True,
            connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
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
        add_missing_columns(connection)
        ensure_target_uniqueness(connection)


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
