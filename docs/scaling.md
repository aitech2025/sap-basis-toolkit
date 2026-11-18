# Running a fleet of SAP systems

What the application does to stay fast and correct with tens of SAP systems, how it was measured, and the numbers
to plan infrastructure with. Measured target: **50 systems, 200 collectors**, 5-minute intervals.

## Measured at 50 systems

Load test: `backend/scripts/loadtest.py` (see the end of this page). PostgreSQL 16, one small VM, simulated systems with
network latency added (150 ms per SOAP call, 1.2 s per SSH login, 40 ms per SQL query), 5 of the 50 systems unreachable
(every call waits for the full timeout, as a host that is down would), 7 days of history.

| | Before | After |
|---|---|---|
| One round of all 200 collectors | 138 s, scheduler blocked meanwhile | **41 s**, scheduler free after 31 ms |
| Overview (all systems) | 1,034 ms, 396 queries | **174 ms** |
| Anomaly detection | 755 ms, **analysed only part of the fleet** (row cap reached) | 2.0 s first call, then cached for 60 s; whole fleet |
| Availability (7 days) | 403 ms | 400 ms first call, then cached |
| Incident correlations | 542 ms | first call similar, then cached |
| HANA monitor, 7-day charts | 677 KB | **91 KB** |
| Monitor snapshot history (7 days) | 907,000 rows, about 880 MB | **195,000 rows** |
| Metric points (7 days) | 3.5 million rows, 600 MB | unchanged (about 2.6 GB at the 30-day default) |

## What the application does

**Scheduling**
- The scheduler hands due collectors to a long-lived pool of `COLLECTOR_MAX_PARALLEL` threads and returns at once. A slow or
  unreachable system ties up one thread until its timeout and never delays the others.
- A collector that is still queued or running is never started a second time. The most overdue collectors go first.
- Inside one sapcontrol run, the instances of a system are queried in parallel (`SAPCONTROL_INSTANCE_PARALLEL`). A system with
  12 application servers, or one with a dead instance, costs about one call's latency instead of the sum.
- Certificate checks run in parallel (`CERT_CHECK_PARALLEL`).
- **One active scheduler.** The worker takes a PostgreSQL advisory lock. Run two scheduler replicas for availability: one
  works, the other stands by and takes over within 30 seconds. If the leader loses its database connection it exits, so work is
  never done twice. Docker Compose restarts it (`restart: unless-stopped`).
- `GET /api/v1/scheduler/health` reports whether collection keeps up: collectors that are `late` (more than a whole interval
  behind), `failing`, `never_run`, and the maximum lag. Alert on `status != "ok"`.

**Reads**
- The current state of each monitor is a pointer (`monitor_latest`), so dashboards no longer scan snapshot history. The cost
  per system is constant, whatever the retention.
- Fleet-wide aggregates (incident counts) are single grouped queries.
- Anomaly detection computes its baseline in one SQL statement over hourly means, so every system is analysed whatever the
  fleet size or collection interval. The baseline is at least 12 hours, from the preceding 7 days.
- Fleet analytics (intelligence, analytics summary) are cached per API process for `INTELLIGENCE_CACHE_SECONDS` (60 s). The
  data changes only when collectors write.
- Monitor charts longer than 24 hours are averaged to at most 360 points per metric.

**Storage**
- Snapshot history older than `SNAPSHOT_FULL_RESOLUTION_HOURS` (24 h) is thinned to one snapshot per hour per monitor. Every
  health change is kept, so root-cause analysis still sees exactly when things turned red. Metric points are not thinned.
- Housekeeping (every 6 hours) applies retention and thinning in batches.

## Sizing (your infrastructure)

**Collector throughput.** Each worker thread is mostly waiting on the network. Rough cost per run: sapcontrol 1–3 s,
HANA SQL about 1 s, SSH inventory about 1.5 s; an unreachable target costs its timeout (15 s).

`needed workers ≈ collectors × average run seconds ÷ interval seconds × 2 (headroom)`

| Systems | Collectors | Interval | `COLLECTOR_MAX_PARALLEL` |
|---|---|---|---|
| 50 | 200 | 300 s | 16 (default; a round takes about 40 s) |
| 50 | 200 | 60 s | 32 |
| 100 | 400 | 300 s | 32 |

Threads are I/O-bound, so raising the count costs little CPU; watch the scheduler's CPU and memory after a change.

**PostgreSQL connections.** Each API process opens up to `DB_POOL_SIZE + DB_MAX_OVERFLOW` (10 + 20). The scheduler opens up
to `COLLECTOR_MAX_PARALLEL + CERT_CHECK_PARALLEL + 4` (28 by default).

`max_connections ≥ API processes × 30 + scheduler replicas × 28 + 20`. For example, 4 API processes and 2 scheduler replicas
need 196; set 200 or put PgBouncer (transaction mode) in front.

**Database size at 50 systems** (defaults: 30-day metrics, 7-day snapshots)
- Metric points: about 500,000 rows/day, roughly 2.6 GB at 30 days (plain PostgreSQL). TimescaleDB compression on chunks older
  than 7 days reduces this by about 90 % if you enable it.
- Monitor snapshots: about 130,000 rows for the last 24 h, then about 11,000/day after thinning. Steady state is about
  200,000 rows, roughly 0.2 GB.
- Autovacuum must keep up with the daily deletes. Default settings are fine at this size; give `metric_points` and
  `monitor_snapshots` a lower `autovacuum_vacuum_scale_factor` (0.05) if you keep longer retention.

**API.** Requests are short; 2–4 uvicorn workers serve dozens of users. The UI polls only incident counts (every 60 s), which is
one grouped query.

**Network.** The scheduler needs to reach every SAP host on 5NN13/5NN14 (sapcontrol), 3NN15 (HANA SQL), 22 (SSH) and 9100
(node_exporter, optional), as allowed by `ALLOWED_TARGET_HOSTS` / `ALLOWED_TARGET_CIDRS`.

## Running the load test

Use an empty database dedicated to the test:

```
createdb sentinel_load
DATABASE_URL=postgresql+psycopg://user@host/sentinel_load alembic upgrade head
cd backend && DATABASE_URL=... python -m scripts.loadtest --systems 50 --down 5 --history-days 7
```

It prints the collection round time, scheduler health, backfill and thinning figures, and response times of the endpoints
the UI calls.
