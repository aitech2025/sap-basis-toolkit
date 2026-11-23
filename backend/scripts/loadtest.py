"""Scale test: N simulated SAP systems with realistic network latency, on the configured database.

    DATABASE_URL=postgresql+psycopg://... SIMULATOR_ENABLED=true python -m scripts.loadtest --systems 50 --history-days 7

What it does
1. Registers N systems through OS discovery (sapcontrol + HANA SQL + os_ssh for the app and DB host = 4 collectors each).
2. Adds latency to the simulator (SOAP call 150 ms, SSH session 1.2 s, SQL query 40 ms). A few systems are made unreachable:
   every call blocks for COLLECTOR_TIMEOUT_SECONDS / SSH_TIMEOUT_SECONDS and then fails, as a down host would.
3. Measures one full collection round through the real scheduler path (runner.run_due), then backfills history at the
   collection interval (metric points and monitor snapshots) server-side, and times the API endpoints the UI calls.

Use a dedicated, empty database: the script creates systems and history in it.
"""
import argparse
import json
import os
import statistics
import sys
import time

os.environ.setdefault("SIMULATOR_ENABLED", "true")
os.environ.setdefault("API_AUTH_TOKEN", "loadtest-key")
os.environ.setdefault("SECRET_KEY", "loadtest-secret-key-123456")
os.environ.setdefault("ALLOWED_TARGET_HOSTS", "*.example.test")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app import config, db  # noqa: E402
from app.services.collectors import runner, simulator, ssh  # noqa: E402

KEY = {"X-API-Key": os.environ["API_AUTH_TOKEN"]}
SOAP_S, SSH_S, SQL_S = 0.150, 1.2, 0.040
DOWN: set[str] = set()


def _down(host: str) -> bool:
    return any(host.startswith(f"simulator-{sid.lower()}") for sid in DOWN)


def add_latency() -> None:
    real_transport = simulator.sapcontrol_transport

    def transport(seed, java=False):
        inner = real_transport(seed, java=java)

        def call(endpoint, method, body):
            if _down(endpoint.host):
                time.sleep(config.COLLECTOR_TIMEOUT_SECONDS)
                raise runner.CollectorError(f"{method}: cannot reach {endpoint.host}:{endpoint.port} (timed out)")
            time.sleep(SOAP_S)
            return inner(endpoint, method, body)
        return call
    simulator.sapcontrol_transport = transport

    real_init = ssh.Session.__init__

    def session_init(self, target):
        if _down(target.host):
            time.sleep(config.SSH_TIMEOUT_SECONDS)
            raise runner.CollectorError(f"Cannot reach {target.host}:22: timed out")
        time.sleep(SSH_S)
        real_init(self, target)
    ssh.Session.__init__ = session_init

    real_query = simulator.SimulatedHana.query
    real_client = runner.hana_client

    def query(self, name, sql, params=()):
        time.sleep(SQL_S)
        return real_query(self, name, sql, params)
    simulator.SimulatedHana.query = query

    def hana_client(row, system):  # hdbcli fails once at connect (connectTimeout), not per query
        if _down(row["host"]):
            time.sleep(config.COLLECTOR_TIMEOUT_SECONDS)
            raise runner.CollectorError("HANA connection failed: timed out")
        return real_client(row, system)
    runner.hana_client = hana_client


def sids(n: int) -> list[str]:
    return [f"L{i:02d}" for i in range(1, n + 1)]


def register_all(c: TestClient, n: int) -> None:
    for i, sid in enumerate(sids(n)):
        host = f"simulator-{sid.lower()}"
        found = c.post("/api/v1/discovery/scan", headers=KEY, json={"method": "os", "host": host, "os_user": f"{sid.lower()}adm",
                                                                     "os_password": "pw"}).json()
        body = {"method": "os", "host": host, "os_user": f"{sid.lower()}adm", "os_password": "pw", "landscape": f"LS{i % 10}",
                "expected_sid": sid, "expected_host_key": found.get("host_key"), "expected_db_host_key": found.get("db_host_key"),
                "environment": ("DEV", "QAS", "PRD")[i % 3],
                "hana": {"host": f"{host}-db", "port": 30015, "username": "MONITOR", "password": "pw"}}
        r = c.post("/api/v1/discovery/register", headers=KEY, json=body)
        if r.status_code != 201:
            sys.exit(f"register {sid} failed: {r.status_code} {r.text[:300]}")


def backfill(days: int, interval_s: int) -> dict:
    """Copy the latest metric values and monitor snapshots back in time at the collection interval (server-side SQL)."""
    eng = db.get_engine()
    with eng.begin() as conn:
        points = conn.execute(text(f"""
            INSERT INTO metric_points (system_id, metric, value, ts)
            SELECT p.system_id, p.metric, p.value * (0.9 + random() * 0.2), g.ts
            FROM (SELECT DISTINCT ON (system_id, metric) system_id, metric, value FROM metric_points
                  ORDER BY system_id, metric, ts DESC) p
            CROSS JOIN generate_series(now() - interval '{days} days', now() - interval '{interval_s} seconds',
                                       interval '{interval_s} seconds') AS g(ts)""")).rowcount
        snaps = conn.execute(text(f"""
            INSERT INTO monitor_snapshots (system_id, monitor, source, health, data, captured_at)
            SELECT s.system_id, s.monitor, s.source, s.health, s.data, g.ts
            FROM (SELECT DISTINCT ON (system_id, monitor) system_id, monitor, source, health, data FROM monitor_snapshots
                  ORDER BY system_id, monitor, id DESC) s
            CROSS JOIN generate_series(now() - interval '{days} days', now() - interval '{interval_s} seconds',
                                       interval '{interval_s} seconds') AS g(ts)
            ORDER BY g.ts""")).rowcount
        size = conn.execute(text("SELECT pg_size_pretty(pg_total_relation_size('monitor_snapshots')), "
                                 "pg_size_pretty(pg_total_relation_size('metric_points'))")).one()
    return {"metric_points_inserted": points, "snapshots_inserted": snaps, "snapshots_size": size[0], "metrics_size": size[1]}


def timed(c: TestClient, path: str, repeat: int = 3) -> dict:
    times, status, size = [], None, 0
    for _ in range(repeat):
        t = time.perf_counter()
        r = c.get(path, headers=KEY)
        times.append((time.perf_counter() - t) * 1000)
        status, size = r.status_code, len(r.content)
    return {"path": path, "status": status, "median_ms": round(statistics.median(times)), "kb": round(size / 1024)}


def endpoints(c: TestClient) -> list[dict]:
    systems = c.get("/api/v1/systems", headers=KEY).json()
    one = systems[len(systems) // 2]["id"]
    paths = ["/api/v1/systems", "/api/v1/overview", "/api/v1/landscapes", "/api/v1/landscapes/LS1/topology",
             "/api/v1/incidents", "/api/v1/incidents/counts", "/api/v1/alerts",
             "/api/v1/analytics/summary?hours=24", "/api/v1/analytics/series?metric=host.memory_used_pct&hours=168&bucket_seconds=3600",
             "/api/v1/intelligence/availability?hours=168", "/api/v1/intelligence/anomalies?hours=24",
             "/api/v1/intelligence/capacity-forecasts", "/api/v1/intelligence/incident-correlations?hours=24",
             f"/api/v1/systems/{one}", f"/api/v1/systems/{one}/overview", f"/api/v1/systems/{one}/monitors",
             f"/api/v1/systems/{one}/monitors/availability?hours=24", f"/api/v1/systems/{one}/monitors/hana?hours=168",
             f"/api/v1/systems/{one}/collectors", f"/api/v1/systems/{one}/infrastructure", f"/api/v1/systems/{one}/instances"]
    return [timed(c, p) for p in paths]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--systems", type=int, default=50)
    ap.add_argument("--down", type=int, default=5, help="systems that time out")
    ap.add_argument("--history-days", type=int, default=7)
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--skip-register", action="store_true")
    ap.add_argument("--rounds", type=int, default=1)
    args = ap.parse_args()
    report: dict = {"systems": args.systems, "parallel": config.COLLECTOR_MAX_PARALLEL}
    with TestClient(__import__("app.main", fromlist=["app"]).app) as c:
        if not args.skip_register:
            t = time.perf_counter()
            register_all(c, args.systems)
            report["register_s"] = round(time.perf_counter() - t, 1)
        add_latency()
        DOWN.update(sids(args.systems)[-args.down:] if args.down else [])
        with db.get_db() as conn:
            report["collectors"] = conn.execute(text("SELECT count(*) FROM collectors WHERE enabled")).scalar()
        for n in range(args.rounds):
            with db.get_db() as conn:  # make every collector due
                conn.execute(text("UPDATE collectors SET last_run_at = NULL"))
            t = time.perf_counter()
            dispatcher = runner.Dispatcher()  # the scheduler's own code path
            submitted = dispatcher.tick()
            tick_ms = round((time.perf_counter() - t) * 1000)
            while dispatcher.inflight():
                time.sleep(0.2)
            dispatcher.shutdown()
            report[f"round{n + 1}"] = {"seconds": round(time.perf_counter() - t, 1), "runs": submitted, "tick_returned_ms": tick_ms,
                                       "failed": dispatcher.failed}
        report["scheduling_health"] = runner.scheduling_health()
        if args.history_days:
            from app.services import monitors
            t = time.perf_counter()
            report["backfill"] = backfill(args.history_days, args.interval)
            t = time.perf_counter()
            report["thinning"] = {"deleted": monitors.thin_snapshots(), "seconds": round(time.perf_counter() - t, 1)}
            with db.get_engine().connect() as conn:
                report["thinning"]["snapshots_rows_after"] = conn.execute(text(
                    "SELECT count(*) FROM monitor_snapshots")).scalar()
        report["endpoints"] = endpoints(c)
    print(json.dumps(report, indent=1, default=str))


if __name__ == "__main__":
    main()
