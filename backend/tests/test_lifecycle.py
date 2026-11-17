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


def _register(client, host="simulator-s4d", env=None):
    payload = {"host": host, "username": "adm", "password": "pw"}
    if env:
        payload["environment"] = env
    return client.post("/api/v1/discovery/register", headers=KEY, json=payload).json()["system"]


def test_control_stop_start_restart(client):
    system = _register(client)
    sid = system["sid"]
    url = f"/api/v1/systems/{system['id']}/control"
    assert client.get(url, headers=KEY).json()["allowed"] is True
    # confirmation and reason are mandatory
    assert client.post(url, headers=KEY, json={"action": "stop", "confirm_sid": "XXX", "reason": "patching"}).status_code == 422
    assert client.post(url, headers=KEY, json={"action": "stop", "confirm_sid": sid, "reason": "x"}).status_code == 422

    op = client.post(url, headers=KEY, json={"action": "stop", "confirm_sid": sid, "reason": "kernel patch",
                                             "soft_timeout": 0, "maintenance_minutes": 30, "wait": True}).json()
    assert op["status"] == "succeeded", op
    assert op["maintenance_window_id"] and op["progress"]
    inst = client.get(f"/api/v1/systems/{system['id']}/instances", headers=KEY).json()
    assert all(i["status"] == "GRAY" for i in inst), [(i["hostname"], i["status"]) for i in inst]

    op = client.post(url, headers=KEY, json={"action": "start", "confirm_sid": sid, "reason": "kernel patch done",
                                             "wait": True}).json()
    assert op["status"] == "succeeded", op
    target = next(i for i in client.get(f"/api/v1/systems/{system['id']}/instances", headers=KEY).json() if i["role"] == "PAS")
    op = client.post(url, headers=KEY, json={"action": "restart", "scope": "instance", "hostname": target["hostname"],
                                             "instance_nr": target["instance_nr"], "confirm_sid": sid,
                                             "reason": "memory leak", "wait": True}).json()
    assert op["status"] == "succeeded" and op["scope"] == "instance", op
    ops = client.get(f"/api/v1/systems/{system['id']}/operations", headers=KEY).json()
    assert [o["action"] for o in ops] == ["restart", "start", "stop"]


def test_control_guards(client, monkeypatch):
    prod = _register(client, "simulator-s4p")
    assert prod["environment"] == "PRD"
    url = f"/api/v1/systems/{prod['id']}/control"
    status = client.get(url, headers=KEY).json()
    assert status["allowed"] is False and "production" in status["reason"]
    assert client.post(url, headers=KEY, json={"action": "stop", "confirm_sid": "S4P", "reason": "test it"}).status_code == 422
    client.patch(f"/api/v1/systems/{prod['id']}", headers=KEY, json={"control_enabled": True})
    assert client.get(url, headers=KEY).json()["allowed"] is True

    # only one running operation per system
    monkeypatch.setattr(config, "CONTROL_POLL_SECONDS", 0.5)
    first = client.post(url, headers=KEY, json={"action": "stop", "confirm_sid": "S4P", "reason": "first op"})
    assert first.status_code == 202
    second = client.post(url, headers=KEY, json={"action": "start", "confirm_sid": "S4P", "reason": "second op"})
    assert second.status_code == 409
    deadline = time.time() + 10
    while client.get(f"/api/v1/operations/{first.json()['id']}", headers=KEY).json()["status"] == "running":
        assert time.time() < deadline
        time.sleep(0.1)

    # roles: operators may not control or discover
    auth.create_user("op1", "Operator-pass-123", "operator")
    token = client.post("/api/v1/auth/login", json={"username": "op1", "password": "Operator-pass-123"}).json()["token"]
    op_headers = {"Authorization": f"Bearer {token}"}
    assert client.post(url, headers=op_headers, json={"action": "start", "confirm_sid": "S4P", "reason": "op"}).status_code == 403
    assert client.post("/api/v1/discovery/scan", headers=op_headers, json={"host": "simulator-s4p"}).status_code == 403
    assert client.patch(f"/api/v1/systems/{prod['id']}", headers=op_headers, json={"control_enabled": False}).status_code == 403
    assert client.get(url, headers=op_headers).status_code == 200

    # globally disabled
    monkeypatch.setattr(config, "CONTROL_ENABLED", False)
    assert "CONTROL_ENABLED" in client.get(url, headers=KEY).json()["reason"]


def test_control_needs_credentials(client):
    r = client.post("/api/v1/discovery/register", headers=KEY, json={"host": "simulator-s4t"}).json()
    status = client.get(f"/api/v1/systems/{r['system']['id']}/control", headers=KEY).json()
    assert status["allowed"] is False and "credentials" in status["reason"]


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
