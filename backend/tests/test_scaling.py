"""Scheduling and query behaviour that matters at fleet size (tens of systems, hundreds of collectors)."""
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.services.collectors import runner
from test_collectors import KEY, make_system, setup


def _collectors(c, system, n):
    return [c.post(f"/api/v1/systems/{system['id']}/collectors", headers=KEY, json={
        "type": "sapcontrol", "host": "simulator", "port": 50013 + 100 * i, "use_tls": False, "interval_seconds": 300}).json()["id"]
        for i in range(n)]


def test_dispatcher_does_not_block_and_never_overlaps_a_collector(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    release = threading.Event()
    started: list[int] = []
    real_run = runner.run

    def slow_run(cid, persist=True):
        started.append(cid)
        if cid == slow_id:
            release.wait(5)  # an unreachable system hanging on its timeout
        return real_run(cid, persist)

    with TestClient(app) as c:
        system = make_system(c)
        ids = _collectors(c, system, 3)
        slow_id = ids[0]
        monkeypatch.setattr(runner, "run", slow_run)
        d = runner.Dispatcher(workers=2)
        t = time.monotonic()
        assert d.tick() == 3
        assert time.monotonic() - t < 1.0  # returns at once, does not wait for the slow run
        deadline = time.monotonic() + 5
        while d.completed < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert d.completed == 2 and d.inflight() == 1  # the fast ones finished while the slow one still runs
        assert d.tick() == 0  # the still-running collector is not submitted a second time; the others are not due
        release.set()
        deadline = time.monotonic() + 5
        while d.inflight() and time.monotonic() < deadline:
            time.sleep(0.05)
        d.shutdown()
        assert sorted(started) == sorted(ids) and len(started) == 3


def test_most_overdue_collectors_go_first_and_lag_is_reported(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with TestClient(app) as c:
        system = make_system(c)
        a, b, new = _collectors(c, system, 3)
        from sqlalchemy import update
        from app.db import collectors, get_db
        now = datetime.now(timezone.utc)
        with get_db() as conn:
            conn.execute(update(collectors).where(collectors.c.id == a).values(last_run_at=now - timedelta(seconds=400), last_status="ok"))
            conn.execute(update(collectors).where(collectors.c.id == b).values(last_run_at=now - timedelta(seconds=2000), last_status="error"))
        assert runner.due_collectors(now) == [new, b, a]
        health = runner.scheduling_health(now)
        assert health["never_run"] == 1 and health["late"] == 1 and health["failing"] == 1 and health["status"] == "behind"
        r = c.get("/api/v1/scheduler/health", headers=KEY)
        assert r.status_code == 200 and r.json()["collectors"] == 3


def test_sapcontrol_instances_are_queried_in_parallel(monkeypatch):
    from app import config
    from app.services.collectors import sapcontrol, simulator
    monkeypatch.setattr(config, "SAPCONTROL_INSTANCE_PARALLEL", 8)
    inner = simulator.sapcontrol_transport("lat:test")

    def slow(endpoint, method, body):
        time.sleep(0.2)
        return inner(endpoint, method, body)
    client = sapcontrol.SapControlClient(sapcontrol.Endpoint("simulator", 50013, False, False, "x", "y"), slow)
    t = time.monotonic()
    raw = sapcontrol.collect(client, timezone.utc)
    elapsed = time.monotonic() - t
    n = len(raw["instances"])
    assert n >= 3
    # serial would be (1 + ~3 calls per instance) x 0.2 s; parallel is about 1 + 3 calls regardless of instance count
    assert elapsed < 0.2 * (1 + 3 * n) * 0.6, elapsed
    assert [i["name"] for i in raw["instances"]] == sorted({i["name"] for i in raw["instances"]}, key=[i["name"] for i in raw["instances"]].index)


def test_latest_pointer_and_snapshot_thinning(monkeypatch, tmp_path):
    from app import config
    from app.db import get_db, monitor_latest, monitor_snapshots
    from app.services import monitors
    from sqlalchemy import func, select
    setup(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    with TestClient(app) as c:
        system = make_system(c)
        sid = system["id"]
        # two days of 5-minute snapshots; one health change in the old part
        for i in range(48 * 12, 0, -1):
            at = now - timedelta(minutes=5 * i)
            monitors.store(sid, "availability", "test", {"i": i}, "critical" if i == 300 else "healthy", at)
        # an upload with an older capture time must not replace the current snapshot
        monitors.store(sid, "availability", "upload", {"old": True}, "healthy", now - timedelta(days=3))
        assert monitors.latest(sid)["availability"]["data"] == {"i": 1}
        with get_db() as conn:
            assert conn.execute(select(func.count()).select_from(monitor_latest)).scalar() == 1
        monkeypatch.setattr(config, "SNAPSHOT_FULL_RESOLUTION_HOURS", 24)
        deleted = monitors.thin_snapshots(now)
        with get_db() as conn:
            rows = conn.execute(select(monitor_snapshots.c.captured_at, monitor_snapshots.c.health)
                                .order_by(monitor_snapshots.c.captured_at)).all()
        old = [r for r in rows if r[0].replace(tzinfo=timezone.utc) < now - timedelta(hours=24)]
        recent = [r for r in rows if r[0].replace(tzinfo=timezone.utc) >= now - timedelta(hours=24)]
        assert len(recent) == 24 * 12  # the last day is untouched
        assert any(h == "critical" for _, h in old)  # state changes survive
        assert 24 <= len(old) <= 24 + 4  # ~one per hour, plus the change and its recovery, plus the old upload
        assert deleted > 200
        assert monitors.thin_snapshots(now) == 0  # idempotent
        assert monitors.latest(sid)["availability"]["data"] == {"i": 1}


def test_incident_counts_are_aggregated(monkeypatch, tmp_path):
    from app.services import incidents
    setup(monkeypatch, tmp_path)
    with TestClient(app) as c:
        system = make_system(c)
        incidents.evaluate(system, {"hana.memory_used_pct": 99.0, "host.disk_used_pct": 90.0})
        by_system = incidents.counts_by_system()[system["id"]]
        assert by_system == incidents.counts(system["id"]) and by_system["total"] == 2
        assert by_system["critical"] == 1 and by_system["warning"] == 1
        assert incidents.worst_active_severity(system["id"]) == "critical"
        overview = c.get("/api/v1/overview", headers=KEY).json()["systems"][0]
        assert overview["incidents"]["total"] == 2
