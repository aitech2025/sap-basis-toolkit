"""Monitoring layers: a system monitors any combination of the SAP application, database and OS layers."""
import pytest
from fastapi.testclient import TestClient

from app.services.collectors import runner, simulator
from test_collectors import KEY, make_system, setup

ALL = ["application", "database", "os"]


@pytest.fixture
def client(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    simulator.reset_state()
    with TestClient(__import__("app.main", fromlist=["app"]).app) as c:
        yield c
    simulator.reset_state()


def add_collector(client, system_id, **body):
    r = client.post(f"/api/v1/systems/{system_id}/collectors", headers=KEY, json={"interval_seconds": 300, **body})
    assert r.status_code == 201, r.text
    return r.json()


def run(client, collector_id):
    return client.post(f"/api/v1/collectors/{collector_id}/run", headers=KEY).json()


def incidents(client, system_id, metric=None, status="active"):
    rows = client.get(f"/api/v1/incidents?status={status}&system_id={system_id}", headers=KEY).json()
    return [i for i in rows if metric is None or i["metric"] == metric]


def os_scan(client, host, **extra):
    return client.post("/api/v1/discovery/scan", headers=KEY,
                       json={"method": "os", "host": host, "os_user": "s4qadm", "os_password": "pw", **extra}).json()


def register(client, found, **extra):
    body = {"method": "os", "host": "simulator-s4q", "os_user": "s4qadm", "os_password": "pw", "landscape": "S4",
            "expected_sid": "S4Q", "expected_host_key": found["host_key"], "expected_db_host_key": found["db_host_key"],
            "hana": {"host": "simulator-s4q-db", "port": 30015, "username": "MONITOR", "password": "pw"}, **extra}
    return client.post("/api/v1/discovery/register", headers=KEY, json=body)


# ------------------------------------------------------------------------------------------------ registry
def test_new_systems_monitor_every_layer_by_default(client):
    system = make_system(client)
    assert system["monitoring_layers"] == ALL


def test_layers_are_validated_and_stored_in_canonical_order(client):
    r = client.post("/api/v1/systems", headers=KEY, json={
        "sid": "ECP", "name": "ECC", "product": "ECC", "stack": "ABAP", "database": "ORACLE", "environment": "PRD",
        "monitoring_layers": ["os", "application", "os"]})
    assert r.status_code == 201, r.text
    assert r.json()["monitoring_layers"] == ["application", "os"]
    sid = r.json()["id"]
    assert client.patch(f"/api/v1/systems/{sid}", headers=KEY, json={"monitoring_layers": []}).status_code == 422
    assert client.patch(f"/api/v1/systems/{sid}", headers=KEY, json={"monitoring_layers": ["network"]}).status_code == 422
    patched = client.patch(f"/api/v1/systems/{sid}", headers=KEY, json={"monitoring_layers": ["database"]})
    assert patched.status_code == 200 and patched.json()["monitoring_layers"] == ["database"]
    # an unrelated edit keeps the selection
    assert client.patch(f"/api/v1/systems/{sid}", headers=KEY, json={"name": "ECC prod"}).json()["monitoring_layers"] == ["database"]


def test_collectors_report_the_layers_they_serve(client):
    system = make_system(client)
    sc = add_collector(client, system["id"], type="sapcontrol", host="simulator", port=50013, use_tls=False)
    sql = add_collector(client, system["id"], type="hana", host="simulator", port=30015, abap_schema="SAPHANADB")
    plain_sql = add_collector(client, system["id"], type="hana", host="simulator", port=30015)
    ssh = add_collector(client, system["id"], type="os_ssh", host="simulator-s4q", port=22, username="root", password="pw")
    assert sc["layers"] == ["application"]
    assert sql["layers"] == ["database", "application"]  # the ABAP schema adds dumps, RFC, IDocs, jobs ...
    assert plain_sql["layers"] == ["database"]
    assert ssh["layers"] == ["os", "database"]  # the database process check on the DB host


# ------------------------------------------------------------------------------------------------ collection
def test_disabled_layers_are_not_scheduled_collected_or_alerted(client):
    system = make_system(client)
    sc = add_collector(client, system["id"], type="sapcontrol", host="simulator", port=50013, use_tls=False)
    sql = add_collector(client, system["id"], type="hana", host="simulator", port=30015, abap_schema="SAPHANADB")
    assert set(runner.due_collectors()) == {sc["id"], sql["id"]}

    client.patch(f"/api/v1/systems/{system['id']}", headers=KEY, json={"monitoring_layers": ["database"]})
    assert runner.due_collectors() == [sql["id"]]  # sapcontrol serves only the application layer
    skipped = run(client, sc["id"])
    assert skipped["status"] == "skipped" and "SAP application" in skipped["error"]

    out = run(client, sql["id"])
    assert out["status"] in ("ok", "partial"), out
    assert any(m.startswith("hana.") for m in out["metrics"])
    assert not any(m.split(".")[0] in ("dumps", "updates", "trfc", "qrfc", "idoc") for m in out["metrics"]), out["metrics"]
    monitors = client.get(f"/api/v1/systems/{system['id']}/monitors", headers=KEY).json()["monitors"]
    assert "hana" in monitors and not {"dumps", "trfc", "availability"} & set(monitors)

    client.patch(f"/api/v1/systems/{system['id']}", headers=KEY, json={"monitoring_layers": ["application"]})
    out = run(client, sql["id"])  # the ABAP schema keeps the SQL collector useful for the application layer
    assert not any(m.startswith("hana.") for m in out["metrics"]), out["metrics"]
    assert "dumps.last_hour" in out["metrics"]


def test_os_collector_honours_os_and_database_layers(client):
    system = make_system(client, sid="S4Q")
    db_host = add_collector(client, system["id"], type="os_ssh", host="simulator-s4q-db", port=22, username="root", password="pw")
    full = run(client, db_host["id"])["metrics"]
    assert {"db.os_process_up", "db.host_memory_used_pct"} <= set(full)

    client.patch(f"/api/v1/systems/{system['id']}", headers=KEY, json={"monitoring_layers": ["database"]})
    only_db = run(client, db_host["id"])["metrics"]
    assert "db.os_process_up" in only_db and not any(m.startswith(("host.", "db.host_")) for m in only_db)

    client.patch(f"/api/v1/systems/{system['id']}", headers=KEY, json={"monitoring_layers": ["os"]})
    only_os = run(client, db_host["id"])["metrics"]
    assert "db.os_process_up" not in only_os and "db.host_memory_used_pct" in only_os


def test_turning_a_layer_off_resolves_its_incidents(client):
    system = make_system(client)
    sql = add_collector(client, system["id"], type="hana", host="simulator", port=30015)
    from app.services import incidents as inc
    full = runner._system(system["id"])
    inc.evaluate(full, {"hana.memory_used_pct": 99.0, "host.memory_used_pct": 99.0})
    assert {i["metric"] for i in incidents(client, system["id"])} == {"hana.memory_used_pct", "host.memory_used_pct"}

    client.patch(f"/api/v1/systems/{system['id']}", headers=KEY, json={"monitoring_layers": ["application", "os"]})
    active = incidents(client, system["id"])
    assert [i["metric"] for i in active] == ["host.memory_used_pct"]
    resolved = incidents(client, system["id"], "hana.memory_used_pct", status="resolved")
    assert resolved and resolved[0]["resolution"] == "Monitoring of the database layer was turned off"
    assert sql["id"] not in runner.due_collectors()


# ------------------------------------------------------------------------------------------------ collector failures
def test_one_failing_collector_keeps_one_stable_incident(client):
    """A healthy collector next to a failing one must not resolve the failure incident on every run (alert flapping)."""
    system = make_system(client, sid="S4Q")
    good = add_collector(client, system["id"], type="sapcontrol", host="simulator", port=50013, use_tls=False)
    bad = add_collector(client, system["id"], type="os_ssh", host="simulator-s4q", port=22, username="root", password="wrong")
    for _ in range(3):
        assert run(client, bad["id"])["status"] == "error"
        assert run(client, good["id"])["status"] in ("ok", "partial")
    failing = incidents(client, system["id"], "collector.failed", status="all")
    assert len(failing) == 1 and failing[0]["status"] == "open", failing

    # pausing the broken collector clears the failure
    client.patch(f"/api/v1/collectors/{bad['id']}", headers=KEY, json={"enabled": False})
    assert incidents(client, system["id"], "collector.failed") == []


def test_failure_of_a_collector_in_a_disabled_layer_is_resolved(client):
    system = make_system(client, sid="S4Q")
    bad = add_collector(client, system["id"], type="os_ssh", host="simulator-s4q", port=22, username="root", password="wrong")
    assert run(client, bad["id"])["status"] == "error"
    assert incidents(client, system["id"], "collector.failed")
    client.patch(f"/api/v1/systems/{system['id']}", headers=KEY, json={"monitoring_layers": ["application"]})
    assert incidents(client, system["id"], "collector.failed") == []


# ------------------------------------------------------------------------------------------------ discovery
def test_discovery_registers_only_the_selected_layers(client):
    found = os_scan(client, "simulator-s4q")
    r = register(client, found, monitoring_layers=["application"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["system"]["monitoring_layers"] == ["application"]
    assert [c["type"] for c in body["collectors"]] == ["sapcontrol"]
    assert any("database layer is not selected" in w for w in body["warnings"]), body["warnings"]
    # discovery still records all three layers in the inventory
    infra = client.get(f"/api/v1/systems/{body['system']['id']}/infrastructure", headers=KEY).json()
    assert infra["database"] and len(infra["hosts"]) == 2


def test_discovery_database_only_checks_the_database_host(client):
    found = os_scan(client, "simulator-s4q")
    body = register(client, found, monitoring_layers=["database"]).json()
    by_type = sorted((c["type"], c["host"]) for c in body["collectors"])
    assert by_type == [("hana", "simulator-s4q-db"), ("os_ssh", "simulator-s4q-db")]
    assert all(run["status"] in ("ok", "partial") for run in body["first_runs"]), body["first_runs"]


def test_discovery_os_only_needs_no_sap_start_service(client):
    found = os_scan(client, "simulator-s4q")
    body = register(client, found, monitoring_layers=["os"], hana=None).json()
    assert sorted(c["type"] for c in body["collectors"]) == ["os_ssh", "os_ssh"]
    assert body["system"]["monitoring_layers"] == ["os"]


def test_discovery_full_monitoring_is_the_default(client):
    found = os_scan(client, "simulator-s4q")
    body = register(client, found).json()
    assert body["system"]["monitoring_layers"] == ALL
    assert sorted(c["type"] for c in body["collectors"]) == ["hana", "os_ssh", "os_ssh", "sapcontrol"]


def test_rediscovery_keeps_the_existing_selection(client):
    found = os_scan(client, "simulator-s4q")
    first = register(client, found, monitoring_layers=["application", "os"]).json()
    # the next scan tells the UI what the registered system monitors, so the picker starts from it
    rescan = os_scan(client, "simulator-s4q")
    assert rescan["existing_systems"][0]["monitoring_layers"] == ["application", "os"]
    again = register(client, rescan, system_id=first["system"]["id"], hana=None).json()
    assert again["system"]["monitoring_layers"] == ["application", "os"]
