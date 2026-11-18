"""OS-level discovery: one SSH login (root or <sid>adm) yields application, database and OS layers."""
import pytest
from fastapi.testclient import TestClient

from app.services.collectors import simulator, ssh
from test_collectors import KEY, setup


@pytest.fixture
def client(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    simulator.reset_state()
    with TestClient(__import__("app.main", fromlist=["app"]).app) as c:
        yield c
    simulator.reset_state()


def scan(client, **body):
    body = {"method": "os", "os_user": "root", "os_password": "pw", **body}
    return client.post("/api/v1/discovery/scan", headers=KEY, json=body)


def test_parsers():
    lines = [
        "LD_LIBRARY_PATH=/usr/sap/S4H/ASCS01/exe; /usr/sap/S4H/ASCS01/exe/sapstartsrv pf=/usr/sap/S4H/SYS/profile/S4H_ASCS01_s4hascs -D -u s4hadm",
        "systemctl --no-ask-password start SAPS4H_00 # sapstartsrv pf=/usr/sap/S4H/SYS/profile/S4H_D00_s4happ1",
        "/usr/sap/x;rm/exe/sapstartsrv pf=/usr/sap/X;Y/SYS/profile/X;Y_D00_evil",
    ]
    found = ssh.parse_sapservices(lines)
    assert [(f["sid"], f["instance_name"], f["profile_host"]) for f in found] == [("S4H", "ASCS01", "s4hascs"), ("S4H", "D00", "s4happ1")]
    os_info = ssh.parse_os(ssh.sections("@@os-release\nPRETTY_NAME=\"SUSE Linux Enterprise Server 15 SP5\"\nNAME=\"SLES\"\n"
                                        "@@uname\nLinux 5.14.21 x86_64\n@@cpu\n32\n@@mem\nMemTotal: 1048576 kB\nMemAvailable: 524288 kB\n"
                                        "@@df\nFilesystem 1024-blocks Used Available Capacity Mounted on\n/dev/sdb 1048576 1 1 81% /usr/sap\n"))
    assert os_info["os_pretty"].startswith("SUSE") and os_info["kernel"] == "5.14.21" and os_info["cpu_count"] == 32
    assert os_info["memory_gb"] == 1.0 and os_info["memory_used_pct"] == 50.0
    assert os_info["filesystems"] == [{"mount": "/usr/sap", "size_gb": 1.0, "used_pct": 81}]
    assert ssh.parse_hostctrl_databases("Instance name: HDB00, Database name: S4H, Database type: hdb, Database status: Running") == [
        {"instance": "HDB00", "name": "S4H", "type": "hdb", "status": "Running"}]
    assert ssh.parse_db_dirs(["/oracle/ECP/19", "/sybase/BWP/ASE-16_0"]) == [
        {"type": "ORACLE", "db_sid": "ECP", "version": "19"}, {"type": "ASE", "db_sid": "BWP", "version": "16.0"}]
    assert not ssh.SID_RE.match("S4;") and ssh.SID_RE.match("S4H")


def test_root_login_detects_three_layers(client):
    r = scan(client, host="simulator-s4d")
    assert r.status_code == 200, r.text
    found = r.json()
    assert found["sid"] == "S4D" and found["method"] == "os"
    assert {s["sid"]: s["kind"] for s in found["sids_on_host"]} == {"DAA": "agent", "S4D": "application"}
    assert found["os"]["os_pretty"] == "SUSE Linux Enterprise Server 15 SP5" and found["os"]["cpu_count"]
    assert found["host_key"].startswith("SHA256:")
    db = found["database_layer"]
    assert db["type"] == "HANA" and db["db_sid"] == "S4H" and db["instance_nr"] == "00" and db["version"].startswith("2.00.079")
    assert db["host"] == "simulator-s4d-db" and db["schema"] == "SAPHANADB" and not db["on_application_host"]
    assert found["db_os"]["os_pretty"] and found["db_host_key"].startswith("SHA256:")
    assert {"ASCS", "ERS", "PAS"} <= {i["role"] for i in found["instances"]}
    assert found["sap_credentials"] is False and any("s4dadm password" in w for w in found["warnings"])


def test_sidadm_login_reuses_credentials_for_sapstartsrv(client):
    found = scan(client, host="simulator-s4d", os_user="s4dadm").json()
    assert found["sap_credentials"] is True and found["control_access"] is True


def test_oracle_database_layer(client):
    found = scan(client, host="simulator-ecd").json()
    db = found["database_layer"]
    assert db["type"] == "ORACLE" and db["version"] == "19" and db["db_sid"] == "ECD"
    assert found["os"]["os_name"] == "Red Hat Enterprise Linux"


def test_scan_errors(client):
    assert "only the HANA database" in scan(client, host="simulator-s4d-db").json()["detail"]
    assert "authentication failed" in scan(client, host="simulator-s4d", os_password="wrong").json()["detail"]
    pinned = scan(client, host="simulator-s4d", expected_host_key="SHA256:somethingelse")
    assert pinned.status_code == 422 and "changed" in pinned.json()["detail"]
    assert "policy" in scan(client, host="sap01.unknown.example.org").json()["detail"]
    assert scan(client, host="simulator-s4d", os_user="", os_password="pw").status_code == 422


def test_register_records_layers_and_schedules_inventory(client):
    found = scan(client, host="simulator-s4q", os_user="s4qadm").json()
    r = client.post("/api/v1/discovery/register", headers=KEY, json={
        "method": "os", "host": "simulator-s4q", "os_user": "s4qadm", "os_password": "pw", "landscape": "S4",
        "expected_sid": "S4Q", "expected_host_key": found["host_key"], "expected_db_host_key": found["db_host_key"],
        "hana": {"host": "simulator-s4q-db", "port": 30015, "username": "MONITOR", "password": "pw"}})
    assert r.status_code == 201, r.text
    body = r.json()
    system = body["system"]
    assert system["sid"] == "S4Q" and system["database"] == "HANA"
    types = sorted(c["type"] for c in body["collectors"])
    assert types == ["hana", "os_ssh", "os_ssh", "sapcontrol"]
    assert all(run["status"] in ("ok", "partial") for run in body["first_runs"]), body["first_runs"]
    hana = next(c for c in body["collectors"] if c["type"] == "hana")
    assert hana["abap_schema"] == "SAPHANADB"  # prefilled from DEFAULT.PFL

    infra = client.get(f"/api/v1/systems/{system['id']}/infrastructure", headers=KEY).json()
    assert infra["application"]["attributes"]["sid"] == "S4Q"
    assert infra["database"]["kind"] == "hana" and infra["database"]["attributes"]["db_sid"] == "S4H"
    roles = sorted(h["attributes"]["role"] for h in infra["hosts"])
    assert roles == ["application", "database"]
    assert all(h["attributes"]["ssh_host_key"].startswith("SHA256:") for h in infra["hosts"])
    rels = sorted(link["relationship"] for link in infra["links"])
    assert rels == ["runs_on", "runs_on", "uses_database"]
    assert all(c["host_key"] for c in infra["collectors"])

    refreshed = client.post(f"/api/v1/systems/{system['id']}/infrastructure/refresh", headers=KEY).json()
    assert all(x["status"] == "ok" for x in refreshed["refresh"])
    # the application host feeds host.* metrics when no node_exporter exists
    series = client.get(f"/api/v1/analytics/series?metric=host.memory_used_pct&hours=1&bucket_seconds=60&system_id={system['id']}", headers=KEY)
    assert series.status_code == 200 and series.json()["series"][0]["points"]

    # a changed host key is refused until an admin trusts the new key
    os_col = next(c for c in infra["collectors"] if c["host"] == "simulator-s4q")
    client.patch(f"/api/v1/collectors/{os_col['id']}", headers=KEY, json={})  # no-op
    from app.db import collectors, get_db
    from sqlalchemy import update
    with get_db() as conn:
        conn.execute(update(collectors).where(collectors.c.id == os_col["id"]).values(host_key="SHA256:old"))
    run = client.post(f"/api/v1/collectors/{os_col['id']}/run", headers=KEY).json()
    assert run["status"] == "error" and "changed" in run["error"]
    client.patch(f"/api/v1/collectors/{os_col['id']}", headers=KEY, json={"trust_new_host_key": True})
    assert client.post(f"/api/v1/collectors/{os_col['id']}/run", headers=KEY).json()["status"] == "ok"
    pinned = next(c for c in client.get(f"/api/v1/systems/{system['id']}/collectors", headers=KEY).json() if c["id"] == os_col["id"])
    assert pinned["host_key"] == found["host_key"] and "password" not in pinned and "private_key_enc" not in pinned


def test_operator_cannot_run_os_discovery(client):
    from app import auth
    auth.create_user("op2", "Operator-pass-123", "operator")
    token = client.post("/api/v1/auth/login", json={"username": "op2", "password": "Operator-pass-123"}).json()["token"]
    r = client.post("/api/v1/discovery/scan", headers={"Authorization": f"Bearer {token}"},
                    json={"method": "os", "host": "simulator-s4d", "os_user": "root", "os_password": "pw"})
    assert r.status_code == 403


# ---- collector coverage matrix: every layer gets live data -------------------------------------------------------

APP_METRICS = {"availability.system_up", "availability.instances_down", "wp.dia_busy_pct"}
DB_METRICS = {"hana.memory_used_pct", "hana.disk_used_pct", "hana.services_inactive", "db.os_process_up"}
OS_METRICS = {"host.memory_used_pct", "host.load1", "host.disk_used_pct",
              "db.host_memory_used_pct", "db.host_load1", "db.host_disk_used_pct"}


def test_collector_matrix_covers_all_three_layers(client):
    found = scan(client, host="simulator-s4q", os_user="s4qadm").json()
    body = client.post("/api/v1/discovery/register", headers=KEY, json={
        "method": "os", "host": "simulator-s4q", "os_user": "s4qadm", "os_password": "pw", "landscape": "S4",
        "expected_sid": "S4Q", "expected_host_key": found["host_key"], "expected_db_host_key": found["db_host_key"],
        "hana": {"host": "simulator-s4q-db", "port": 30015, "username": "MONITOR", "password": "pw"}}).json()
    system = body["system"]
    seen: dict[str, set] = {}
    for col in body["collectors"]:
        out = client.post(f"/api/v1/collectors/{col['id']}/run", headers=KEY).json()
        assert out["status"] in ("ok", "partial"), (col["type"], out)
        seen.setdefault(col["type"], set()).update(out["metrics"])
    every = set().union(*seen.values())
    assert APP_METRICS <= every, APP_METRICS - every
    assert DB_METRICS <= every, DB_METRICS - every
    assert OS_METRICS <= every, OS_METRICS - every
    # who feeds what: sapcontrol = application, hana SQL + os_ssh(DB host) = database, os_ssh = OS of both hosts
    assert APP_METRICS <= seen["sapcontrol"] and {"hana.memory_used_pct", "hana.services_inactive"} <= seen["hana"]
    assert {"db.os_process_up", "host.memory_used_pct", "db.host_memory_used_pct"} <= seen["os_ssh"]
    # every emitted metric is in the analytics catalogue (so it can be charted and alerted on)
    catalogue = {m["key"] for m in (lambda j: j["metrics"] if isinstance(j, dict) else j)(client.get("/api/v1/analytics/metrics", headers=KEY).json())}
    missing = {m for m in every if m != "collector.failed"} - catalogue
    assert not missing, sorted(missing)

    # HANA availability from the database's own sapstartsrv (optional collector, needs hdbadm credentials)
    hs = client.post(f"/api/v1/systems/{system['id']}/collectors", headers=KEY, json={
        "type": "hana_sapcontrol", "host": "simulator-s4q-db", "port": 50013, "username": "s4hadm", "password": "pw",
        "interval_seconds": 300}).json()
    out = client.post(f"/api/v1/collectors/{hs['id']}/run", headers=KEY).json()
    assert out["metrics"]["hana.system_up"] == 1.0

    # node_exporter takes over host.* for the application host; os_ssh then only reports the DB host
    simulator_ne = client.post(f"/api/v1/systems/{system['id']}/collectors", headers=KEY, json={
        "type": "node_exporter", "host": "simulator-s4q", "port": 9100, "interval_seconds": 300})
    if simulator_ne.status_code in (200, 201):
        app_os = next(c for c in body["collectors"] if c["type"] == "os_ssh" and c["host"] == "simulator-s4q")
        out = client.post(f"/api/v1/collectors/{app_os['id']}/run", headers=KEY).json()
        assert "host.memory_used_pct" not in out["metrics"]


def test_host_role_from_host_evidence():
    assert ssh.host_role({"processes": ["disp+work", "hdbindexserver"]}, []) == "application + database"
    assert ssh.host_role({"processes": []}, [{"instance_name": "HDB00"}]) == "database"  # stopped DB is still the DB host
    assert ssh.host_role({"processes": ["sapstartsrv"]}, [{"instance_name": "D00"}]) == "application"
    assert ssh.database_running({"processes": ["ora_pmon_ECP"]}, "ORACLE") is True
    assert ssh.database_running({"processes": ["sapstartsrv"]}, "HANA") is False
    assert ssh.database_running({"processes": []}, "MAXDB") is None


def test_manual_os_collector_on_db_host_checks_database(client):
    """An os_ssh collector added by hand (no discovery) still recognises the database host from what runs on it."""
    from test_collectors import make_system
    system = make_system(client, sid="S4Q")
    col = client.post(f"/api/v1/systems/{system['id']}/collectors", headers=KEY, json={
        "type": "os_ssh", "host": "simulator-s4q-db", "port": 22, "username": "root", "password": "pw", "interval_seconds": 300}).json()
    out = client.post(f"/api/v1/collectors/{col['id']}/run", headers=KEY).json()
    assert out["status"] == "ok", out
    assert out["metrics"]["db.os_process_up"] == 1.0 and "host.memory_used_pct" not in out["metrics"]
