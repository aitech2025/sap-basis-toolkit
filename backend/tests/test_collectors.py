import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.db import collectors, get_db
from app.main import app
from app.services import monitors, notifications
from app.services.collectors import CollectorError, hana, runner, sapcontrol, simulator
from sqlalchemy import select

KEY = {"X-API-Key": "test-secret"}

PROCESS_LIST = """<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" xmlns:SAPControl="urn:SAPControl">
<SOAP-ENV:Body><SAPControl:GetProcessListResponse><process>
<item><name>disp+work</name><description>Dispatcher</description><dispstatus>SAPControl-GREEN</dispstatus>
<textstatus>Running</textstatus><starttime>2026 11 01 10:00:00</starttime><elapsedtime>400:00:01</elapsedtime><pid>4711</pid></item>
<item><name>icman</name><description>ICM</description><dispstatus>SAPControl-YELLOW</dispstatus>
<textstatus>Running, Message Server connection ok</textstatus><starttime></starttime><elapsedtime></elapsedtime><pid>4712</pid></item>
</process></SAPControl:GetProcessListResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>"""

FAULT = """<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"><SOAP-ENV:Body>
<SOAP-ENV:Fault><faultcode>SOAP-ENV:Client</faultcode><faultstring>Permission denied</faultstring></SOAP-ENV:Fault>
</SOAP-ENV:Body></SOAP-ENV:Envelope>"""


def setup(monkeypatch, tmp_path, simulator_on=True):
    monkeypatch.setattr(db, "DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(config, "API_AUTH_TOKEN", "test-secret")
    monkeypatch.setattr(config, "SECRET_KEY", "unit-test-secret")
    monkeypatch.setattr(config, "ALLOWED_TARGET_HOSTS", ("*.corp.example.test",))
    monkeypatch.setattr(config, "ALLOWED_TARGET_CIDRS", ("10.20.0.0/16",))
    monkeypatch.setattr(config, "SIMULATOR_ENABLED", simulator_on)
    db.reset_engine_for_tests()
    sent = []
    monkeypatch.setattr(config, "ALERT_WEBHOOK_URL", "https://hooks.example.test")
    monkeypatch.setattr(notifications, "send_webhook", lambda payload: (sent.append(payload) or True, None))
    return sent


def make_system(c, sid="S4P", database="HANA", product="S4HANA"):
    return c.post("/api/v1/systems", headers=KEY, json={"sid": sid, "name": sid, "product": product, "stack": "ABAP",
                                                         "database": database, "environment": "PRD"}).json()


def test_soap_parsing_and_faults():
    items = sapcontrol.parse_items(PROCESS_LIST)
    assert [i["name"] for i in items] == ["disp+work", "icman"]
    assert items[1]["dispstatus"] == "SAPControl-YELLOW" and items[0]["pid"] == "4711"
    with pytest.raises(CollectorError, match="Permission denied"):
        sapcontrol.parse_items(FAULT)
    with pytest.raises(CollectorError, match="Invalid SOAP"):
        sapcontrol.parse_items("<html>login</html")


def test_workprocess_and_availability_builders(monkeypatch):
    monkeypatch.setattr(config, "WP_LONG_RUNNING_SECONDS", 1800)
    wps = [{"type": "DIA", "status": "Run", "time_s": 2500, "err": 0, "no": "0"}] + \
          [{"type": "DIA", "status": "Run", "time_s": 5, "err": 0, "no": str(i)} for i in range(1, 4)] + \
          [{"type": "DIA", "status": "Wait", "err": 1, "no": "9"}, {"type": "BTC", "status": "Stopped", "err": 2, "no": "10"}]
    data, metrics, health = monitors.build_workprocesses([{"instance": "app1_00", "workprocesses": wps}])
    assert data["summary"]["by_type"]["DIA"] == {"total": 5, "busy": 4, "stopped": 0, "busy_pct": 80.0}
    assert metrics == {"wp.long_running": 1.0, "wp.stopped": 1.0, "wp.dia_busy_pct": 80.0, "wp.btc_busy_pct": 0.0}
    assert data["summary"]["restarts"] == 3 and health == "warning"
    inst = [{"hostname": "ascs", "features": "MESSAGESERVER|ENQUE", "status": "SAPControl-GREEN"},
            {"hostname": "app1", "features": "ABAP|GATEWAY", "status": "SAPControl-GRAY"},
            {"hostname": "app2", "features": "ABAP|GATEWAY", "status": "SAPControl-GREEN"}]
    data, metrics, health = monitors.build_availability(inst)
    assert metrics == {"availability.system_up": 1.0, "availability.instances_down": 1.0} and health == "warning"
    inst[2]["status"] = "SAPControl-RED"
    _, metrics, health = monitors.build_availability(inst)
    assert metrics["availability.system_up"] == 0.0 and health == "critical"


def test_hana_collect_with_fake_client_and_partial_failures():
    class Fake(simulator.SimulatedHana):
        def query(self, name, sql, params=()):
            if name == "updates":
                raise RuntimeError("invalid column name: VBRC")
            assert ('"SAPHANADB".' in sql) == (name in ("dumps", "updates", "trfc", "qrfc_out", "qrfc_in", "idoc_errors",
                                                       "idoc_total", "jobs", "imports"))
            return super().query(name, sql, params)
    out = hana.collect(Fake("S4P:x:1"), "SAPHANADB", timezone.utc)
    failed = [d for d in out["diagnostics"] if not d["ok"]]
    assert [d["call"] for d in failed] == ["updates"] and "VBRC" in failed[0]["error"]
    assert "updates" not in out and out["hana"]["memory"]["limit"] > 0 and out["dumps"]
    assert all(d["error"] != "unknown" for d in out["dumps"])  # FLIST runtime error extracted
    data, metrics, health = monitors.build_hana(out["hana"])
    assert 0 < metrics["hana.memory_used_pct"] < 100 and metrics["hana.replication_ok"] == 1.0
    assert "hana.data_backup_age_h" in metrics and "hana.log_backup_age_h" in metrics
    # without an ABAP schema only database views are read
    out = hana.collect(simulator.SimulatedHana("x"), None, timezone.utc)
    assert {d["call"] for d in out["diagnostics"]} == {"version", "memory", "cpu", "disk", "alerts", "backups", "replication", "services", "active_statements", "service_threads", "expensive_statements"}
    with pytest.raises(CollectorError):
        hana.collect(simulator.SimulatedHana("x"), "sap;drop", timezone.utc)


def test_dump_error_extraction():
    assert hana._dump_error("FC008TIME_OUTAP012SAPMV45A") == "TIME_OUT"
    assert hana._dump_error(b"FC025DBSQL_DUPLICATE_KEY_ERRORxx") == "DBSQL_DUPLICATE_KEY_ERROR"
    assert hana._dump_error("garbage") == "unknown"


def test_simulated_collectors_end_to_end(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with TestClient(app) as c:
        system = make_system(c)
        sc = c.post(f"/api/v1/systems/{system['id']}/collectors", headers=KEY, json={
            "type": "sapcontrol", "host": "simulator", "port": 50013, "use_tls": False, "interval_seconds": 60}).json()
        hc = c.post(f"/api/v1/systems/{system['id']}/collectors", headers=KEY, json={
            "type": "hana", "host": "simulator", "port": 30015, "abap_schema": "saphanadb", "username": "MONITOR",
            "password": "secret-password", "interval_seconds": 300}).json()
        assert hc["abap_schema"] == "SAPHANADB" and hc["has_password"] is True and "password" not in hc
        with get_db() as conn:
            stored = conn.execute(select(collectors.c.password_enc).where(collectors.c.id == hc["id"])).scalar_one()
        assert stored and "secret-password" not in stored  # encrypted at rest
        r1 = c.post(f"/api/v1/collectors/{sc['id']}/run", headers=KEY).json()
        r2 = c.post(f"/api/v1/collectors/{hc['id']}/run", headers=KEY).json()
        assert r1["status"] in ("ok", "partial") and r2["status"] == "ok", (r1, r2)
        mons = c.get(f"/api/v1/systems/{system['id']}/monitors", headers=KEY).json()["monitors"]
        assert {"availability", "workprocesses", "syslog", "hana", "dumps", "trfc", "qrfc", "idocs"} <= set(mons)
        assert mons["availability"]["source"] == "simulator"
        wp = c.get(f"/api/v1/systems/{system['id']}/monitors/workprocesses", headers=KEY).json()
        assert wp["latest"]["data"]["summary"]["by_type"]["DIA"]["total"] > 0 and wp["series"]["wp.dia_busy_pct"]
        # HANA collector also feeds jobs, import history and growth
        assert c.get(f"/api/v1/systems/{system['id']}/jobs/report", headers=KEY).json()["summary"]["runs"] > 0
        assert c.get(f"/api/v1/systems/{system['id']}/transports/history", headers=KEY).json()["stats"]["imports"] == 4
        assert c.get(f"/api/v1/systems/{system['id']}/growth/report", headers=KEY).json()["tracked_tables"] == 10
        listed = c.get(f"/api/v1/systems/{system['id']}/collectors", headers=KEY).json()
        assert all(x["last_run_at"] for x in listed) and all("password_enc" not in x for x in listed)


def test_unreachable_collector_raises_incident_and_recovers(monkeypatch, tmp_path):
    sent = setup(monkeypatch, tmp_path)
    with TestClient(app) as c:
        system = make_system(c)
        col = c.post(f"/api/v1/systems/{system['id']}/collectors", headers=KEY, json={
            "type": "sapcontrol", "host": "sapprd.corp.example.test", "port": 50013, "use_tls": False}).json()

        def down(endpoint, method, body):
            raise CollectorError(f"{method}: cannot reach {endpoint.host}:{endpoint.port} (Connection refused)")
        monkeypatch.setattr(sapcontrol, "http_transport", down)
        r = c.post(f"/api/v1/collectors/{col['id']}/run", headers=KEY).json()
        assert r["status"] == "error" and "Connection refused" in r["error"]
        inc = c.get(f"/api/v1/incidents?system_id={system['id']}", headers=KEY).json()
        assert inc[0]["metric"] == "collector.failed" and len(sent) == 1
        monkeypatch.setattr(sapcontrol, "http_transport", simulator.sapcontrol_transport("x"))
        assert c.post(f"/api/v1/collectors/{col['id']}/run", headers=KEY).json()["status"] in ("ok", "partial")
        assert c.get(f"/api/v1/incidents?system_id={system['id']}", headers=KEY).json() == []


def test_collector_validation_and_policy(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path, simulator_on=False)
    with TestClient(app) as c:
        system = make_system(c)
        ecc = make_system(c, "ECP", "ORACLE", "ECC")
        url = f"/api/v1/systems/{system['id']}/collectors"
        assert "5<NN>13" in c.post(url, headers=KEY, json={"type": "sapcontrol", "host": "a.corp.example.test", "port": 8080}).json()["detail"]
        assert "disabled" in c.post(url, headers=KEY, json={"type": "sapcontrol", "host": "simulator", "port": 50013}).json()["detail"]
        assert "not HANA" in c.post(f"/api/v1/systems/{ecc['id']}/collectors", headers=KEY,
                                    json={"type": "hana", "host": "db.corp.example.test", "port": 30015, "username": "M"}).json()["detail"]
        # host outside the allowlist is blocked at connection time and reported, never contacted
        test = c.post(url + "/test", headers=KEY, json={"type": "sapcontrol", "host": "evil.example.com", "port": 50013,
                                                        "use_tls": False}).json()
        assert test["ok"] is False and "outbound policy" in test["error"]


def test_upload_fallbacks_for_non_hana_systems(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    today = datetime.now(timezone.utc).strftime("%d.%m.%Y")
    now_t = datetime.now(timezone.utc).strftime("%H:%M:%S")
    files = {
        "dumps": f"Date;Time;Application Server;User;Client;Runtime Error\n" + "".join(
            f"{today};{now_t};app1;JSMITH;100;TIME_OUT\n" for _ in range(22)),
        "syslog": f"Date;Time;Instance;User;MNo;Message Text\n{today};{now_t};app1;SAPSYS;AB0;Run-time error TIME_OUT occurred\n",
        "updates": f"Client;User;Date;Time;Tcode;Status\n100;JSMITH;{today};{now_t};VA01;Err\n100;BOB;{today};{now_t};ME21N;Init\n",
        "trfc": f"Caller;Function Module;Target System;Date;Time;Status Text\nALEREMOTE;IDOC_INBOUND_ASYNCHRONOUS;BWP_100;{today};{now_t};SYSFAIL: Name or password incorrect\n",
        "qrfc": "Client;Queue Name;Destination;Entries;Status\n100;R3AD_MAT01;CRM_100;250;SYSFAIL\n100;CSA_ORDER;CRM_100;3;READY\n",
        "idocs": f"IDoc number;Status;Message Type;Direction;Created on;Time\n0000000000123;51;ORDERS;2;{today};{now_t}\n0000000000124;53;ORDERS;2;{today};{now_t}\n",
    }
    with TestClient(app) as c:
        ecc = make_system(c, "ECP", "ORACLE", "ECC")
        results = {m: c.post(f"/api/v1/systems/{ecc['id']}/monitors/{m}/upload", headers=KEY, content=body).json() for m, body in files.items()}
        assert results["dumps"]["metrics"]["dumps.last_hour"] == 22 and results["dumps"]["health"] == "warning"
        assert results["syslog"]["metrics"]["syslog.errors_last_hour"] == 1
        assert results["updates"]["metrics"]["updates.errors"] == 1
        assert results["trfc"]["metrics"]["trfc.errors"] == 1
        assert results["qrfc"]["metrics"]["qrfc.blocked_queues"] == 1
        assert results["idocs"]["metrics"]["idoc.errors_24h"] == 1
        titles = [i["title"] for i in c.get(f"/api/v1/incidents?system_id={ecc['id']}", headers=KEY).json()]
        assert any("Short dumps" in t for t in titles) and any("Blocked qRFC" in t for t in titles)
        assert c.post(f"/api/v1/systems/{ecc['id']}/monitors/hana/upload", headers=KEY, content="x").status_code == 404


def test_scheduler_runs_only_due_collectors(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with TestClient(app) as c:
        system = make_system(c)
        col = c.post(f"/api/v1/systems/{system['id']}/collectors", headers=KEY, json={
            "type": "sapcontrol", "host": "simulator", "port": 50013, "use_tls": False, "interval_seconds": 300}).json()
        assert runner.due_collectors() == [col["id"]]
        runner.run_due()
        assert runner.due_collectors() == []
        assert runner.due_collectors(datetime.now(timezone.utc) + timedelta(seconds=301)) == [col["id"]]
        c.patch(f"/api/v1/systems/{system['id']}", headers=KEY, json={"enabled": False})
        assert runner.due_collectors(datetime.now(timezone.utc) + timedelta(seconds=301)) == []


def test_java_system_gets_no_abap_work_process_monitors(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with TestClient(app) as c:
        java = c.post("/api/v1/systems", headers=KEY, json={"sid": "EPP", "name": "Portal", "product": "PORTAL", "stack": "JAVA",
                                                             "database": "ORACLE", "environment": "PRD"}).json()
        col = c.post(f"/api/v1/systems/{java['id']}/collectors", headers=KEY, json={"type": "sapcontrol", "host": "simulator", "port": 50013, "use_tls": False}).json()
        assert c.post(f"/api/v1/collectors/{col['id']}/run", headers=KEY).json()["status"] == "ok"
        mons = c.get(f"/api/v1/systems/{java['id']}/monitors", headers=KEY).json()["monitors"]
        assert set(mons) == {"availability"}
        procs = [p["name"] for i in mons["availability"]["data"]["instances"] for p in i["processes"]]
        assert "jstart" in procs and "disp+work" not in procs


def test_live_availability_drives_landscape_health(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with TestClient(app) as c:
        system = make_system(c)
        assert c.get("/api/v1/overview", headers=KEY).json()["systems"][0]["health"] == "unknown"
        data, _, health = monitors.build_availability([
            {"hostname": "ascs", "features": "MESSAGESERVER|ENQUE", "status": "SAPControl-GREEN"},
            {"hostname": "app1", "features": "ABAP", "status": "SAPControl-GREEN"}])
        monitors.store(system["id"], "availability", "sapcontrol", data, health)
        assert c.get("/api/v1/overview", headers=KEY).json()["systems"][0]["health"] == "healthy"
        data, _, health = monitors.build_availability([
            {"hostname": "ascs", "features": "MESSAGESERVER|ENQUE", "status": "SAPControl-GREEN"},
            {"hostname": "app1", "features": "ABAP", "status": "SAPControl-RED"}])
        monitors.store(system["id"], "availability", "sapcontrol", data, health)
        overview = c.get("/api/v1/overview", headers=KEY).json()["systems"][0]
        assert overview["health"] == "critical" and overview["availability"]["system_up"] is False


def test_hana_statement_and_thread_metrics_are_summary_only():
    data, metrics, _ = monitors.build_hana({
        "active_statements": {"count": 12, "long_running": 2, "max_duration_s": 81.5},
        "service_threads": {"count": 220, "waiting": 27},
        "expensive_statements_1h": {"count": 8, "max_duration_s": 120.0},
    })
    assert metrics["hana.active_statements"] == 12
    assert metrics["hana.long_running_statements"] == 2
    assert metrics["hana.waiting_threads"] == 27
    assert metrics["hana.expensive_statements_1h"] == 8
    assert data["active_statements"]["max_duration_s"] == 81.5
    # SQL text and statement literals are never collected into monitor snapshots.
    assert "statement_string" not in str(data).lower()
