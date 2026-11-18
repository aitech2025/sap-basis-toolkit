"""Discovery (host -> registered system + instances + monitoring) and start/stop/restart control."""
import time

import pytest
from fastapi.testclient import TestClient

from app import auth, config, db
from app.services import discovery
from app.services.collectors import simulator

from test_collectors import KEY, setup


@pytest.fixture
def client(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    simulator.reset_state()
    monkeypatch.setattr(simulator, "TRANSITION_SCALE", 0.02)
    monkeypatch.setattr(config, "CONTROL_POLL_SECONDS", 0.05)
    monkeypatch.setattr(config, "CONTROL_TIMEOUT_SECONDS", 10)
    with TestClient(__import__("app.main", fromlist=["app"]).app) as c:
        yield c
    simulator.reset_state()


def test_role_classification():
    assert discovery.classify_role("ASCS01", "MESSAGESERVER|ENQUE") == ("ASCS", "instance_name")
    assert discovery.classify_role("ERS02", "ENQREP") == ("ERS", "instance_name")
    assert discovery.classify_role("DVEBMGS00", "ABAP|GATEWAY") == ("PAS", "instance_name")
    assert discovery.classify_role("D10", "ABAP") == ("AAS", "instance_name")
    assert discovery.classify_role("J00", "J2EE") == ("JAVA", "instance_name")
    assert discovery.classify_role("W00", "WEBDISP") == ("WEBDISP", "instance_name")
    assert discovery.classify_role(None, "MESSAGESERVER|ENQUE", java_only=True) == ("SCS", "features")
    assert discovery.classify_role(None, "ENQREP") == ("ERS", "features")
    rows = discovery.assign_roles([
        {"hostname": "b", "instance_nr": "02", "instance_name": "D02", "features": "ABAP"},
        {"hostname": "a", "instance_nr": "00", "instance_name": "D00", "features": "ABAP"},
        {"hostname": "c", "instance_nr": "01", "instance_name": "ASCS01", "features": "MESSAGESERVER|ENQUE"}])
    assert {r["instance_name"]: r["role"] for r in rows} == {"D00": "PAS", "D02": "AAS", "ASCS01": "ASCS"}
    assert next(r for r in rows if r["instance_name"] == "D00")["role_source"] == "inferred"


def test_product_detection():
    s4 = discovery.detect_product([{"component": "SAP_BASIS", "release": "758"}, {"component": "S4CORE", "release": "108"}], "ABAP")
    assert (s4["product"], s4["product_version"], s4["basis_release"]) == ("S4HANA", "2023", "7.58")
    ecc = discovery.detect_product([{"component": "SAP_APPL", "release": "618"}], "ABAP")
    assert (ecc["product"], ecc["product_version"]) == ("ECC", "EHP8")
    assert discovery.detect_product([], "JAVA")["product"] == "NW_JAVA"
    assert discovery.suggest_environment("S4P") == "PRD" and discovery.suggest_environment("ECD") == "DEV"


def test_scan_detects_sid_instances_product(client):
    r = client.post("/api/v1/discovery/scan", headers=KEY,
                    json={"host": "simulator-s4p", "username": "s4padm", "password": "pw"})
    assert r.status_code == 200, r.text
    found = r.json()
    assert found["sid"] == "S4P" and found["product"] == "S4HANA" and found["product_version"] == "2023"
    assert found["database"] == "HANA" and found["stack"] == "ABAP" and found["control_access"] is True
    roles = [i["role"] for i in found["instances"]]
    assert roles[:2] == ["ASCS", "ERS"] and "PAS" in roles
    assert found["suggested_environment"] == "PRD"

    ecc = client.post("/api/v1/discovery/scan", headers=KEY, json={"host": "simulator-ecd"}).json()
    assert ecc["product"] == "ECC" and ecc["database"] == "ORACLE" and ecc["control_access"] is None
    assert any("No OS credentials" in w for w in ecc["warnings"])

    java = client.post("/api/v1/discovery/scan", headers=KEY, json={"host": "simulator-epq"}).json()
    assert java["stack"] == "JAVA" and java["product"] == "NW_JAVA"
    assert [i["role"] for i in java["instances"]][:2] == ["SCS", "ERS"]

    bad = client.post("/api/v1/discovery/scan", headers=KEY, json={"host": "unknown.example.org"})
    assert bad.status_code == 422 and "policy" in bad.json()["detail"]


def test_register_starts_monitoring_and_inventory(client):
    r = client.post("/api/v1/discovery/register", headers=KEY, json={
        "host": "simulator-s4q", "username": "s4qadm", "password": "pw", "expected_sid": "S4Q",
        "landscape": "S4", "role_overrides": {"sapapp2:01": "AAS"}})
    assert r.status_code == 201, r.text
    body = r.json()
    system = body["system"]
    assert system["sid"] == "S4Q" and system["environment"] == "QAS" and system["product"] == "S4HANA"
    assert body["created"] and body["first_runs"][0]["status"] in ("ok", "partial")
    inst = client.get(f"/api/v1/systems/{system['id']}/instances", headers=KEY).json()
    assert {i["role"] for i in inst} >= {"ASCS", "ERS", "PAS"}
    manual = next(i for i in inst if i["hostname"] == "sapapp2")
    assert manual["role_source"] == "manual"
    # availability monitor ran
    mon = client.get(f"/api/v1/systems/{system['id']}/monitors/availability", headers=KEY)
    assert mon.status_code == 200 and mon.json()["latest"]["source"] == "simulator"
    # re-register updates the same system instead of duplicating it, manual role survives
    again = client.post("/api/v1/discovery/register", headers=KEY, json={
        "host": "simulator-s4q", "username": "s4qadm", "password": "pw", "system_id": system["id"]}).json()
    assert again["created"] is False and len(again["collectors"]) == 1
    assert next(i for i in again["instances"] if i["hostname"] == "sapapp2")["role"] == "AAS"
    # wrong SID guard
    wrong = client.post("/api/v1/discovery/register", headers=KEY, json={"host": "simulator-s4q", "expected_sid": "XYZ"})
    assert wrong.status_code == 422
    # role edit + refresh
    pas = next(i for i in inst if i["role"] == "PAS")
    edited = client.patch(f"/api/v1/systems/{system['id']}/instances/{pas['id']}", headers=KEY, json={"role": "AAS"})
    assert edited.json()["role"] == "AAS"
    refreshed = client.post(f"/api/v1/systems/{system['id']}/instances/refresh", headers=KEY).json()
    assert next(i for i in refreshed if i["id"] == pas["id"])["role"] == "AAS"


def _register(client, host="simulator-s4d", env=None, db=True, sql=True):
    sid = host.split("-", 1)[1]
    payload = {"host": host, "username": "adm", "password": "pw"}
    if env:
        payload["environment"] = env
    if db:
        payload["db_control"] = {"username": f"{sid[:2]}hadm", "password": "pw"}
    if sql:
        payload["hana"] = {"host": f"simulator-{sid}-db", "port": 30015, "username": "MONITOR", "password": "pw",
                           "abap_schema": "SAPHANADB"}
    r = client.post("/api/v1/discovery/register", headers=KEY, json=payload)
    assert r.status_code == 201, r.text
    return r.json()["system"]


def _control(client, system, **body):
    body.setdefault("confirm_sid", system["sid"])
    body.setdefault("reason", "planned maintenance")
    body.setdefault("wait", True)
    r = client.post(f"/api/v1/systems/{system['id']}/control", headers=KEY, json=body)
    assert r.status_code in (202, 409, 422), r.text
    return r


def _actions(op):
    return [s["key"] for s in op["steps"] if s["kind"] == "action"]


def test_scan_finds_hana_instance(client):
    found = client.post("/api/v1/discovery/scan", headers=KEY, json={"host": "simulator-s4d", "username": "a", "password": "b"}).json()
    hi = found["hana_instance"]
    assert hi and hi["instance_nr"] == "00" and hi["sid"] == "S4H" and hi["suggested_user"] == "s4hadm"
    assert hi["instances"][0]["role"] == "HANA"
    ecc = client.post("/api/v1/discovery/scan", headers=KEY, json={"host": "simulator-ecd"}).json()
    assert ecc["hana_instance"] is None and ecc["database"] == "ORACLE"


def test_full_system_stop_and_start_follow_sap_sequence(client):
    system = _register(client)
    inst = client.get(f"/api/v1/systems/{system['id']}/instances", headers=KEY).json()
    assert any(i["role"] == "HANA" for i in inst)
    caps = client.get(f"/api/v1/systems/{system['id']}/control", headers=KEY).json()
    assert caps["allowed"] and caps["scopes"] == {"system": True, "app": True, "db": True, "instance": True}

    plan = client.get(f"/api/v1/systems/{system['id']}/control/plan?action=stop&scope=system", headers=KEY).json()
    assert not plan["blocked"] and [m["key"] for m in plan["manual"]] == ["users_locked", "jobs_suspended", "interfaces_stopped", "backup_ok"]

    op = _control(client, system, action="stop", scope="system", soft_timeout=0, maintenance_minutes=30,
                  checklist={"users_locked": True, "jobs_suspended": True, "interfaces_stopped": True, "backup_ok": True}).json()
    assert op["status"] == "succeeded", op["message"]
    assert _actions(op) == ["stop_aas", "stop_pas", "stop_cs", "stop_ers", "stop_db"]
    finished = [s["finished_at"] for s in op["steps"] if s["kind"] == "action"]
    assert finished == sorted(finished)
    assert all(s["status"] == "confirmed" for s in op["steps"] if s["key"].startswith("pre_"))
    inst = client.get(f"/api/v1/systems/{system['id']}/instances", headers=KEY).json()
    assert all(i["status"] == "GRAY" for i in inst), [(i["hostname"], i["status"]) for i in inst]

    op = _control(client, system, action="start", scope="system").json()
    assert op["status"] == "succeeded", op["message"]
    keys = [s["key"] for s in op["steps"] if s["kind"] in ("action", "check")]
    assert keys.index("start_db") < keys.index("check_sql") < keys.index("start_cs") < keys.index("start_ers") \
        < keys.index("start_pas") < keys.index("start_aas")
    todo = [s for s in op["steps"] if s["status"] == "todo"]
    assert len(todo) == 4 and "follow-up" in op["message"]
    done = client.post(f"/api/v1/operations/{op['id']}/steps/post_jobs_released/confirm", headers=KEY, json={}).json()
    assert next(s for s in done["steps"] if s["key"] == "post_jobs_released")["status"] == "confirmed"

    op = _control(client, system, action="restart", scope="system").json()
    assert op["status"] == "succeeded", op["message"]
    assert _actions(op)[:5] == ["stop_aas", "stop_pas", "stop_cs", "stop_ers", "stop_db"]
    assert _actions(op)[5] == "start_db"


def test_database_guards(client):
    system = _register(client)
    sid = system["id"]
    # HANA cannot be stopped while application servers run
    plan = client.get(f"/api/v1/systems/{sid}/control/plan?action=stop&scope=db", headers=KEY).json()
    assert plan["blocked"] and "Still running" in plan["blockers"][0]
    op = _control(client, system, action="stop", scope="db").json()
    assert op["status"] == "failed" and _actions(op) == ["stop_db"]
    assert next(s for s in op["steps"] if s["key"] == "stop_db")["status"] == "skipped"

    # stop the application, then the database alone
    assert _control(client, system, action="stop", scope="app").json()["status"] == "succeeded"
    op = _control(client, system, action="stop", scope="db").json()
    assert op["status"] == "succeeded", op["message"]
    # starting the application while HANA is down is blocked ...
    plan = client.get(f"/api/v1/systems/{sid}/control/plan?action=start&scope=app", headers=KEY).json()
    assert plan["blocked"] and any("HANA is not running" in b for b in plan["blockers"])
    # ... and forcing it makes the application servers fail (RED), halting the procedure
    op = _control(client, system, action="start", scope="app", force=True).json()
    assert op["status"] == "failed" and "RED" in op["message"]
    assert next(s for s in op["steps"] if s["key"] == "check_db_up")["status"] == "overridden"
    # database first, then the application: works
    assert _control(client, system, action="start", scope="db").json()["status"] == "succeeded"
    op = _control(client, system, action="start", scope="app").json()
    assert op["status"] == "succeeded", op["message"]


def test_pending_updates_block_stop(client, monkeypatch):
    system = _register(client)
    original = simulator.SimulatedHana.query
    monkeypatch.setattr(simulator.SimulatedHana, "query",
                        lambda self, name, sql, params=(): [{"N": 3}] if name == "pending_updates" else original(self, name, sql, params))
    op = _control(client, system, action="stop", scope="app").json()
    assert op["status"] == "failed" and "SM13" in op["message"]
    assert all(s["status"] == "skipped" for s in op["steps"] if s["kind"] == "action")
    op = _control(client, system, action="stop", scope="app", force=True).json()
    assert op["status"] == "succeeded"
    assert next(s for s in op["steps"] if s["key"] == "check_updates")["status"] == "overridden"


def test_instance_restart_and_abort(client, monkeypatch):
    system = _register(client)
    pas = next(i for i in client.get(f"/api/v1/systems/{system['id']}/instances", headers=KEY).json() if i["role"] == "PAS")
    op = _control(client, system, action="restart", scope="instance", hostname=pas["hostname"], instance_nr=pas["instance_nr"]).json()
    assert op["status"] == "succeeded" and _actions(op) == ["stop_pas", "start_pas"], op["message"]

    monkeypatch.setattr(config, "CONTROL_POLL_SECONDS", 0.3)
    monkeypatch.setattr(simulator, "TRANSITION_SCALE", 5)
    r = _control(client, system, action="stop", scope="app", wait=False)
    op_id = r.json()["id"]
    time.sleep(0.5)
    assert client.post(f"/api/v1/operations/{op_id}/abort", headers=KEY).status_code == 200
    deadline = time.time() + 10
    while (op := client.get(f"/api/v1/operations/{op_id}", headers=KEY).json())["status"] == "running":
        assert time.time() < deadline
        time.sleep(0.1)
    assert op["status"] == "aborted" and "partially" in op["message"]


def test_control_guards(client, monkeypatch):
    prod = _register(client, "simulator-s4p")
    assert prod["environment"] == "PRD"
    url = f"/api/v1/systems/{prod['id']}/control"
    status = client.get(url, headers=KEY).json()
    assert status["allowed"] is False and "production" in status["reason"]
    assert client.post(url, headers=KEY, json={"action": "stop", "confirm_sid": "S4P", "reason": "test it"}).status_code == 422
    client.patch(f"/api/v1/systems/{prod['id']}", headers=KEY, json={"control_enabled": True})
    assert client.get(url, headers=KEY).json()["allowed"] is True
    assert client.post(url, headers=KEY, json={"action": "stop", "confirm_sid": "XXX", "reason": "patching"}).status_code == 422
    assert client.post(url, headers=KEY, json={"action": "stop", "confirm_sid": "S4P", "reason": "x"}).status_code == 422

    # only one running operation per system
    monkeypatch.setattr(config, "CONTROL_POLL_SECONDS", 0.5)
    first = client.post(url, headers=KEY, json={"action": "stop", "scope": "app", "confirm_sid": "S4P", "reason": "first op"})
    assert first.status_code == 202
    second = client.post(url, headers=KEY, json={"action": "start", "scope": "app", "confirm_sid": "S4P", "reason": "second op"})
    assert second.status_code == 409
    deadline = time.time() + 15
    while client.get(f"/api/v1/operations/{first.json()['id']}", headers=KEY).json()["status"] == "running":
        assert time.time() < deadline
        time.sleep(0.1)

    # roles: operators may not control, plan, abort or discover
    auth.create_user("op1", "Operator-pass-123", "operator")
    token = client.post("/api/v1/auth/login", json={"username": "op1", "password": "Operator-pass-123"}).json()["token"]
    op_headers = {"Authorization": f"Bearer {token}"}
    assert client.post(url, headers=op_headers, json={"action": "start", "confirm_sid": "S4P", "reason": "op"}).status_code == 403
    assert client.get(url + "/plan?action=stop", headers=op_headers).status_code == 403
    assert client.post(f"/api/v1/operations/{first.json()['id']}/abort", headers=op_headers).status_code == 403
    assert client.post("/api/v1/discovery/scan", headers=op_headers, json={"host": "simulator-s4p"}).status_code == 403
    assert client.patch(f"/api/v1/systems/{prod['id']}", headers=op_headers, json={"control_enabled": False}).status_code == 403
    assert client.get(url, headers=op_headers).status_code == 200

    monkeypatch.setattr(config, "CONTROL_ENABLED", False)
    assert "CONTROL_ENABLED" in client.get(url, headers=KEY).json()["reason"]


def test_control_needs_credentials(client):
    r = client.post("/api/v1/discovery/register", headers=KEY, json={"host": "simulator-s4t"}).json()
    status = client.get(f"/api/v1/systems/{r['system']['id']}/control", headers=KEY).json()
    assert status["allowed"] is False and "credentials" in status["reason"]


def test_hana_db_collector_monitors_database(client):
    system = _register(client)
    cols = client.get(f"/api/v1/systems/{system['id']}/collectors", headers=KEY).json()
    db = next(c for c in cols if c["type"] == "hana_sapcontrol")
    assert db["last_status"] == "ok"
    assert client.post(f"/api/v1/collectors/{db['id']}/run", headers=KEY).json()["status"] == "ok"


def test_interrupted_operations_marked(client):
    system = _register(client)
    from app.db import get_db, system_operations
    from app.services import control
    from datetime import datetime, timezone
    with get_db() as conn:
        conn.execute(system_operations.insert().values(system_id=system["id"], action="stop", scope="system",
                                                       reason="x", requested_by="admin", status="running",
                                                       started_at=datetime.now(timezone.utc)))
    assert control.mark_interrupted() == 1
    assert control.list_operations(system["id"])[0]["status"] == "interrupted"
