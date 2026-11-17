# Time-series telemetry storage

Sentinel stores configuration, users, incidents, audit history and operational records in PostgreSQL.
Numeric collector observations are written to the dedicated `metric_points` table.

## Storage architecture

- **TimescaleDB (recommended production mode):** the Compose database image is TimescaleDB on PostgreSQL 16. Migration `20261117_0008` enables the extension and converts `metric_points` into a hypertable partitioned by `ts`, using one-day chunks.
- **PostgreSQL fallback:** if an externally managed PostgreSQL server does not provide the TimescaleDB extension, the migration leaves `metric_points` as a normal indexed PostgreSQL table. The application API and collectors continue to work.
- **SQLite development/test mode:** uses the same table as an ordinary table. No TimescaleDB dependency is required for CI or local unit tests.

The legacy `metric_samples` table is retained and its existing observations are copied into `metric_points` during the migration. New metric writes, time-window reads and retention cleanup use `metric_points`.

## Why TimescaleDB

TimescaleDB retains PostgreSQL's SQL and relational model while adding time partitioning and time-series query optimizations. This fits Sentinel's current workload: metrics are scoped to SAP systems, queried by metric and time range, and correlated with relational system/incident metadata. It avoids operating a second database and avoids requiring PromQL for the application's first analytics dashboards.

## Retention and operations

- `METRIC_RETENTION_DAYS` controls application-managed deletion of old metric points (default: 30 days).
- `SNAPSHOT_RETENTION_DAYS` independently controls JSON monitor snapshot retention.
- The existing scheduler invokes monitor pruning; retention therefore remains consistent across TimescaleDB, PostgreSQL fallback and SQLite.
- Keep database backups and test restore procedures before production rollout. Hypertable chunks are still part of the PostgreSQL database backup.

## Deployment

For the bundled Compose deployment, the `db` service uses `timescale/timescaledb:2.17.2-pg16`. Existing named PostgreSQL volumes are not automatically converted between PostgreSQL image distributions. For an existing deployment, take and verify a logical backup, stop the stack, migrate/restore into a fresh TimescaleDB volume, and only then run `alembic upgrade head`. Do not point the TimescaleDB image at an unverified existing PostgreSQL data directory.

For managed PostgreSQL, install and enable a TimescaleDB version compatible with PostgreSQL 16 before migration. If that is not possible, Sentinel falls back to ordinary PostgreSQL storage without losing application functionality.
