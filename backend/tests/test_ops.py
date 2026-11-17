from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app import config, db
from app.main import app
from app.services import notifications

H = {"X-API-Key": "test-secret"}
SAMPLES = Path(__file__).resolve().parents[2] / "samples"


def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(config, "API_AUTH_TOKEN", "test-secret")
    monkeypatch.setattr(config, "ALLOWED_TARGET_HOSTS", ("qa.example.test",))
    db.reset_engine_for_tests()
    sent = []
    monkeypatch.setattr(config, "ALERT_WEBHOOK_URL", "https://hooks.example.test")
    monkeypatch.setattr(notifications, "send_webhook", lambda payload: (sent.append(payload) or True, None))
    return sent


def system(client, sid="S4P", stack="ABAP", product="S4HANA", database="HANA", tz="Europe/Berlin"):
    r = client.post("/api/v1/systems", headers=H, json={"sid": sid, "name": sid, "product": product, "stack": stack,
                                                         "database": database, "environment": "PRD", "timezone": tz})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_timezone_is_validated(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with TestClient(app) as c:
        bad = c.post("/api/v1/systems", headers=H, json={"sid": "S4P", "name": "x", "product": "S4HANA", "stack": "ABAP",
                                                         "database": "HANA", "environment": "PRD", "timezone": "Mars/Base"})
        assert bad.status_code == 422 and "timezone" in bad.json()["detail"]


def test_transport_history_is_stored_deduplicated_and_feeds_precheck(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with TestClient(app) as c:
        sid = system(c)
        log = (SAMPLES / "stms_import_history_full.csv").read_text()
        r = c.post(f"/api/v1/systems/{sid}/transports/postcheck", headers=H, json={"log": log, "alert": False}).json()
        assert r["history"] == {"stored": 6, "duplicates": 0}
        again = c.post(f"/api/v1/systems/{sid}/transports/postcheck", headers=H, json={"log": log, "alert": False}).json()
        assert again["history"] == {"stored": 0, "duplicates": 6}
        h = c.get(f"/api/v1/systems/{sid}/transports/history?days=3650", headers=H).json()
        assert h["stats"]["imports"] == 6 and h["stats"]["requests"] == 5 and h["stats"]["failed"] == 2
        first = h["imports"][0]
        assert first["trkorr"] == "S4DK900105" and first["rc"] == 12 and first["result"] == "fatal"
        assert first["owner"] == "DAVE" and first["description"] == "MM: release strategy"
        # 16.11.2026 21:01:47 Berlin (CET, UTC+1) = 20:01:47 UTC
        assert first["imported_at"].startswith("2026-11-16T20:01:47")
        failed = c.get(f"/api/v1/systems/{sid}/transports/history?days=3650&result=failed", headers=H).json()
        assert {i["trkorr"] for i in failed["imports"]} == {"S4DK900103", "S4DK900105"}
        # untimed tp log text is accepted and de-duplicated within a day
        txt = "import of S4DK900200 finished, return code: 4\n"
        c.post(f"/api/v1/systems/{sid}/transports/postcheck", headers=H, json={"log": txt, "alert": False})
        r = c.post(f"/api/v1/systems/{sid}/transports/postcheck", headers=H, json={"log": txt, "alert": False}).json()
        assert r["history"]["duplicates"] == 1
        # pre-check uses the stored history for the missing-predecessor check
        e070 = "TRKORR,TRSTATUS,AS4DATE,AS4TIME\nS4DK900101,R,20261101,100000\nS4DK900090,R,20261020,100000\nS4DK900300,R,20261117,100000\n"
        e071 = "TRKORR,OBJECT,OBJ_NAME\nS4DK900090,CLAS,ZCL_X\nS4DK900300,CLAS,ZCL_X\nS4DK900101,PROG,ZP\n"
        pre = c.post(f"/api/v1/systems/{sid}/transports/precheck", headers=H,
                     json={"e071_csv": e071, "e070_csv": e070, "queue": ["S4DK900300"]}).json()
        assert any(f["code"] == "MISSING_PREDECESSOR" and "S4DK900090" in f["message"] for f in pre["findings"])
        assert any("stored import history" in n for n in pre["notes"])


def test_job_failures_report_upsert_and_alerts(monkeypatch, tmp_path):
    sent = setup(monkeypatch, tmp_path)
    captured = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)  # 13:00 Berlin
    with TestClient(app) as c:
        sid = system(c)
        csv = (SAMPLES / "sm37_jobs.csv").read_text()
        r = c.post(f"/api/v1/systems/{sid}/jobs/snapshots", headers=H, params={"captured_at": captured.isoformat()}, content=csv)
        assert r.status_code == 201 and r.json() == {"rows": 9, "inserted": 9, "updated": 0, "new_failures": 3}
        # one alert per job name per day
        assert sorted(p["summary"] for p in sent) == [
            "[SAP Basis Toolkit] S4P (default): background job Z_FI_PAYMENT_RUN cancelled (2 runs)",
            "[SAP Basis Toolkit] S4P (default): background job Z_MM_MRP_RUN cancelled"]
        # re-upload: active job now finished, nothing duplicated, no new alerts
        csv2 = csv.replace("21000300;BATCH_SD;Active;17.11.2026;08:00:00;", "21000300;BATCH_SD;Finished;17.11.2026;08:00:00;7.300")
        r2 = c.post(f"/api/v1/systems/{sid}/jobs/snapshots", headers=H, params={"captured_at": captured.isoformat()}, content=csv2).json()
        assert r2["inserted"] == 0 and r2["updated"] == 1 and r2["new_failures"] == 0 and len(sent) == 2

        monkeypatch.setattr("app.services.ops.datetime", _FrozenDatetime(captured))
        report = c.get(f"/api/v1/systems/{sid}/jobs/report?days=7", headers=H).json()
        s = report["summary"]
        assert s["runs"] == 9 and s["cancelled"] == 3 and s["finished"] == 6 and s["failing_jobs"] == 2
        top = report["failing_jobs"][0]
        assert top["job_name"] == "Z_FI_PAYMENT_RUN" and top["cancelled"] == 2 and top["still_failing"] is True
        assert top["failure_rate_pct"] == 66.7
        mrp = report["failing_jobs"][1]
        assert mrp["job_name"] == "Z_MM_MRP_RUN" and mrp["still_failing"] is False
        assert any(f["duration_s"] == 1234 for f in report["recent_failures"])  # "1.234" seconds


class _FrozenDatetime(datetime):
    """datetime.now() frozen for report windows; everything else behaves like datetime."""
    def __new__(cls, frozen):
        obj = datetime.__new__(cls, frozen.year, frozen.month, frozen.day, frozen.hour, frozen.minute, tzinfo=frozen.tzinfo)
        cls._frozen = frozen
        return obj

    def __call__(self, *args, **kwargs):
        return datetime(*args, **kwargs)

    @classmethod
    def now(cls, tz=None):
        return cls._frozen

    @classmethod
    def fromisoformat(cls, value):
        return datetime.fromisoformat(value)

    @classmethod
    def combine(cls, *args, **kwargs):
        return datetime.combine(*args, **kwargs)


def test_long_running_job_detection(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=5)).astimezone(timezone.utc)
    csv = f"JOBNAME;JOBCOUNT;STATUS;STRTDATE;STRTTIME\nZ_LONG;1;R;{start:%Y%m%d};{start:%H%M%S}\nZ_SHORT;2;R;{now:%Y%m%d};{now:%H%M%S}\n"
    with TestClient(app) as c:
        sid = system(c, tz="UTC")
        c.post(f"/api/v1/systems/{sid}/jobs/snapshots", headers=H, content=csv)
        report = c.get(f"/api/v1/systems/{sid}/jobs/report", headers=H).json()
        assert [j["job_name"] for j in report["long_running"]] == ["Z_LONG"]
        assert 299 <= report["long_running"][0]["running_minutes"] <= 301
        overview = c.get(f"/api/v1/systems/{sid}/overview", headers=H).json()
        assert overview["jobs"]["health"] == "warning" and overview["jobs"]["long_running"] == 1
        assert overview["jobs"]["report"]["long_running"][0]["job_name"] == "Z_LONG"


def test_lock_snapshots_ages_persistence_and_alerts(monkeypatch, tmp_path):
    sent = setup(monkeypatch, tmp_path)
    # SM12 times are Berlin local; capture at 14:45 Berlin = 13:45 UTC
    first = datetime(2026, 11, 17, 13, 15, tzinfo=timezone.utc)
    second = datetime(2026, 11, 17, 13, 45, tzinfo=timezone.utc)
    csv = (SAMPLES / "sm12_locks.csv").read_text()
    with TestClient(app) as c:
        sid = system(c)
        r1 = c.post(f"/api/v1/systems/{sid}/locks/snapshots", headers=H, params={"captured_at": first.isoformat()},
                    content=csv.replace("100;KTHOMAS;14:30:10;S;EKKO;1004500000123\n", "")).json()
        assert r1["locks"] == 4
        r2 = c.post(f"/api/v1/systems/{sid}/locks/snapshots", headers=H, params={"captured_at": second.isoformat()}, content=csv).json()
        assert r2["locks"] == 5 and r2["long_running"] == 3
        monkeypatch.setattr("app.services.ops.datetime", _FrozenDatetime(second + timedelta(minutes=1)))
        report = c.get(f"/api/v1/systems/{sid}/locks/report?history_days=90", headers=H).json()
        s = report["summary"]
        assert s["locks"] == 5 and s["users"] == 4 and s["tables"] == 5 and s["snapshots"] == 2
        oldest = report["long_running"][0]
        # 08:00:31 Berlin = 07:00:31 UTC, captured 13:45 UTC -> 404 minutes, critical (>= 240)
        assert oldest["user"] == "BATCH_SD" and oldest["age_minutes"] == 404 and oldest["severity"] == "critical"
        assert [x["severity"] for x in report["long_running"]] == ["critical", "critical", "critical"]
        assert {p["user"] for p in report["persistent"]} == {"JSMITH", "BATCH_SD", "MMUELLER"}
        assert report["by_user"][0] == {"user": "JSMITH", "locks": 2}
        assert [h["total"] for h in report["history"]] == [4, 5]
        # alerts: JSMITH VBAK + VBAP, BATCH_SD VBRK are critical; MMUELLER (47 min) none; no duplicates
        summaries = [p["summary"] for p in sent]
        assert len(summaries) == 3 and all("held by" in x for x in summaries)
        assert report["stale"] is False
        # an empty SM12 list is a valid snapshot
        empty = c.post(f"/api/v1/systems/{sid}/locks/snapshots", headers=H, content=b"").json()
        assert empty["locks"] == 0


def test_lock_time_without_date_rolls_back_a_day(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    captured = datetime(2026, 11, 17, 0, 30, tzinfo=timezone.utc)  # 00:30 UTC
    with TestClient(app) as c:
        sid = system(c, tz="UTC")
        c.post(f"/api/v1/systems/{sid}/locks/snapshots", headers=H, params={"captured_at": captured.isoformat()},
               content="User;Time;Table\nNIGHT;23:30:00;BKPF\n")
        monkeypatch.setattr("app.services.ops.datetime", _FrozenDatetime(captured + timedelta(minutes=1)))
        lock = c.get(f"/api/v1/systems/{sid}/locks/report?history_days=90", headers=H).json()["long_running"][0]
        assert lock["age_minutes"] == 60 and lock["locked_at"].startswith("2026-11-16T23:30")


def test_java_systems_reject_jobs_and_locks(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with TestClient(app) as c:
        sid = system(c, "EPP", "JAVA", "PORTAL", "ORACLE")
        r = c.post(f"/api/v1/systems/{sid}/jobs/snapshots", headers=H, content="Job Name;Status\nX;Finished\n")
        assert r.status_code == 409 and "Java Scheduler" in r.json()["detail"]
        assert c.get(f"/api/v1/systems/{sid}/locks/report", headers=H).status_code == 409
        o = c.get(f"/api/v1/systems/{sid}/overview", headers=H).json()
        assert o["transport_history"] is None and o["jobs"]["health"] == "unknown"
