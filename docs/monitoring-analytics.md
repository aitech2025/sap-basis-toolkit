# Monitoring analytics

Sentinel's Monitoring Analytics workspace queries numeric observations from the `metric_points` time-series table. The workspace is read-only and does not fabricate or interpolate telemetry.

## Capabilities

- Metric catalogue grouped by Availability, Work processes, ABAP errors, Interfaces and HANA.
- Landscape-wide or single-system trend exploration.
- Time windows from 1 hour to 30 days.
- Bucket sizes from 1 minute to 1 day.
- Average, minimum, maximum and latest-in-bucket aggregation.
- Per-system trend cards with latest, average and peak values.
- Summary coverage: enabled systems, systems reporting, observations, distinct metrics and latest observation time.

## API

All endpoints require the existing Sentinel authentication:

- `GET /api/v1/analytics/metrics` — metric definitions and units.
- `GET /api/v1/analytics/summary?hours=24&landscape=Finance` — collection coverage and freshness.
- `GET /api/v1/analytics/series?metric=hana.cpu_pct&hours=24&bucket_seconds=300&aggregation=avg&landscape=Finance` — grouped trend series. Optional `system_id` narrows to one system.

Allowed bucket sizes: 60, 300, 900, 1800, 3600, 21600 and 86400 seconds. Allowed aggregations: `avg`, `min`, `max`, `last`. Queries are bounded to 30 days and 100,000 returned buckets/observations.

## Query execution

PostgreSQL deployments use database-side bucket aggregation. When TimescaleDB is enabled, Sentinel uses `time_bucket` so the hypertable can prune time chunks efficiently. Vanilla PostgreSQL uses an epoch-based bucket expression. SQLite development/test mode uses the same API contract with a bounded in-process aggregation fallback.

The API does not sum snapshot gauges or interpolate missing intervals. `availability.system_up` is stored as 1/0 and displayed as a percentage of observed samples, not as a mathematically exact SLA. Work-process utilisation and HANA CPU/memory/disk are sampled gauges. Error counts and backup age are latest-snapshot values; average/min/max describe the observed snapshots within each bucket.

## Data quality

- No points means no collector/upload observations for that metric and period.
- A gap means no observation was received; it is not treated as zero.
- Each bucket includes its raw sample count.
- The summary's reporting coverage is based on actual metric rows within the selected time window.
- Collector interval and SAP/HANA source availability determine trend resolution. A 1-minute bucket does not create 1-minute source sampling.

## Future extensions

The current phase provides trend exploration and cross-system comparison. Follow-on work can add alert overlays, SLO/uptime calculations with explicit observation semantics, capacity forecasting, anomaly detection and exportable operational reports.
