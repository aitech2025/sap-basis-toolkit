from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer, MetaData, String,
    Table, Text, create_engine, inspect, text,
)
from sqlalchemy.engine import Connection, Engine

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


def ensure_target_uniqueness(connection: Connection) -> None:
    """Safely enforce endpoint uniqueness on fresh and legacy databases."""
    inspector = inspect(connection)
    if "targets" not in inspector.get_table_names():
        return

    index_names = {item["name"] for item in inspector.get_indexes("targets")}
    constraint_names = {
        item.get("name") for item in inspector.get_unique_constraints("targets")
    }
    if "uq_targets_hostname_port" in index_names or "uq_targets_hostname_port" in constraint_names:
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

    Index(
        "uq_targets_hostname_port", targets.c.hostname, targets.c.port, unique=True
    ).create(bind=connection, checkfirst=True)


def init_db() -> None:
    engine = get_engine()
    # Preflight existing targets before metadata.create_all attempts to create
    # the unique index. This produces an actionable error for legacy duplicates.
    with engine.begin() as connection:
        ensure_target_uniqueness(connection)
    metadata.create_all(engine)
    with engine.begin() as connection:
        ensure_target_uniqueness(connection)


def reset_engine_for_tests() -> None:
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None
