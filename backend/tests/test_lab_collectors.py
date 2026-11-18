"""Collector integration tests against real services (opt-in: SENTINEL_LAB=1, after tests/lab/setup_lab.sh).

Unlike the simulator tests these go through the real transports end to end:
- OS layer:          real OpenSSH (root and a tcsh <sid>adm user, password and key) and the real node_exporter binary.
- Application layer: sapcontrol over real HTTP with Basic auth against a sapstartsrv stand-in that answers with the
                     SOAP documents sapstartsrv sends (namespaces, item lists, faults, HTTP 401).
- Database layer:    HANA SQL through HanaClient with an hdbcli stand-in (cursor/description protocol of SAP's driver),
                     the HANA sapstartsrv (hana_sapcontrol) and the DB process check over SSH (os_ssh on the DB host).
"""
import base64
import os
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from app import config
from app.services.collectors import simulator
from test_collectors import KEY, setup

pytestmark = pytest.mark.skipif(os.getenv("SENTINEL_LAB") != "1", reason="lab services not running (set SENTINEL_LAB=1)")

LAB = "saplab"
SIDADM = ("s4ladm", "Lab-pass-123")
ENV = ('<?xml version="1.0" encoding="UTF-8"?>\n<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" '
       'xmlns:SOAP-ENC="http://schemas.xmlsoap.org/soap/encoding/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
       'xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:SAPControl="urn:SAPControl" xmlns:SAPCCMS="urn:SAPCCMS" '
       'xmlns:SAPHostControl="urn:SAPHostControl" xmlns:SAPOscol="urn:SAPOscol" xmlns:SAPDSR="urn:SAPDSR">'
       '<SOAP-ENV:Body>{}</SOAP-ENV:Body></SOAP-ENV:Envelope>')


def _items(wrapper, rows):
    return f"<{wrapper}>" + "".join("<item>" + "".join(f"<{k}>{v}</{k}>" for k, v in r.items()) + "</item>" for r in rows) + f"</{wrapper}>"


def _proc(name, desc, status="GREEN"):
    return {"name": name, "description": desc, "dispstatus": f"SAPControl-{status}", "textstatus": "Running",
            "starttime": "2026 11 18 06:00:01", "elapsedtime": "4:12:30", "pid": "4711"}


ABAP_INSTANCES = [
    {"hostname": LAB, "instanceNr": 1, "httpPort": 50113, "httpsPort": 50114, "startPriority": "1",
     "features": "MESSAGESERVER|ENQUE", "dispstatus": "SAPControl-GREEN"},
    {"hostname": LAB, "instanceNr": 0, "httpPort": 50013, "httpsPort": 50014, "startPriority": "3",
     "features": "ABAP|GATEWAY|ICMAN|IGS", "dispstatus": "SAPControl-GREEN"},
]
HANA_INSTANCES = [{"hostname": LAB, "instanceNr": 2, "httpPort": 50213, "httpsPort": 50214, "startPriority": "0.3",
                   "features": "HDB|HDB_WORKER", "dispstatus": "SAPControl-GREEN"}]
PROCESSES = {
    50113: [_proc("msg_server", "MessageServer"), _proc("enq_server", "Enqueue Server 2")],
    50013: [_proc("disp+work", "Dispatcher"), _proc("igswd_mt", "IGS Watchdog"), _proc("gwrd", "Gateway"), _proc("icman", "ICM")],
    50213: [_proc("hdbdaemon", "HDB Daemon"), _proc("hdbnameserver", "HDB Nameserver"), _proc("hdbindexserver", "HDB Indexserver-S4L"),
            _proc("hdbxsengine", "HDB XSEngine-S4L", "YELLOW")],
}
WP = [{"No": i, "Typ": t, "Pid": 1000 + i, "Status": s, "Reason": "", "Start": "yes", "Err": "", "Sem": "", "Cpu": "",
       "Time": tm, "Program": prog, "Client": "100" if prog else "", "User": "BATCHUSER" if prog else "", "Action": "", "Table": ""}
      for i, (t, s, tm, prog) in enumerate([("DIA", "Wait", "", ""), ("DIA", "Run", "12", "SAPMSSY1"), ("BTC", "Run", "2405", "RSBTCDEL2"),
                                            ("UPD", "Wait", "", ""), ("SPO", "Wait", "", "")])]
SYSLOG = [{"Time": "2026 11 18 10:01:12", "Text": "BZY Transaction Canceled 00 671 ( ABAP Programming Error )", "Severity": "1",
           "MNo": "AB0", "User": "DDIC", "Client": "000", "Program": "SAPMSSY1"}]
FAULT = ('<SOAP-ENV:Fault><faultcode>SOAP-ENV:Server</faultcode><faultstring>Permission denied</faultstring></SOAP-ENV:Fault>')


class SapStartSrv(BaseHTTPRequestHandler):
    """Answers like sapstartsrv: Basic auth for protected methods, SOAP faults, per-instance ports."""

    def log_message(self, *a):
        pass

    def do_POST(self):
        port = self.server.server_address[1]
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        method = self.headers.get("SOAPAction", "").strip('"').split("/")[-1]
        auth = self.headers.get("Authorization", "")
        ok_auth = auth == "Basic " + base64.b64encode(f"{SIDADM[0]}:{SIDADM[1]}".encode()).decode()
        if method != "GetSystemInstanceList" and not ok_auth:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="SAP Start Service"')
            self.end_headers()
            return
        assert f"<ns1:{method}>" in body or f"<ns1:{method}/>" in body or f"<ns1:{method}></ns1:{method}>" in body
        if method == "GetSystemInstanceList":
            payload = _items("instance", HANA_INSTANCES if port == 50213 else ABAP_INSTANCES)
        elif method == "GetProcessList":
            payload = _items("process", PROCESSES[port])
        elif method == "ABAPGetWPTable" and port == 50013:
            payload = _items("workprocess", WP)
        elif method == "ABAPReadSyslog" and port == 50013:
            payload = _items("log", SYSLOG)
        else:
            out = ENV.format(FAULT).encode()
            self.send_response(500)
            self.send_header("Content-Type", "text/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return
        out = ENV.format(f"<SAPControl:{method}Response>{payload}</SAPControl:{method}Response>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture(scope="module")
def startsrv():
    servers = [ThreadingHTTPServer(("127.0.0.1", p), SapStartSrv) for p in (50013, 50113, 50213)]
    for s in servers:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    yield
    for s in servers:
        s.shutdown()


class _Cursor:
    """hdbcli cursor protocol: execute(sql, params), description, fetchall()."""
    ROWS = {
        "M_DATABASE": (["VERSION"], [("2.00.077.00.1710325774",)]),
        "M_HOST_RESOURCE_UTILIZATION": (["USED", "LIMIT"], [(48 * 2**30, 64 * 2**30)]),
        "M_DISKS": (["USED", "TOTAL"], [(300 * 2**30, 500 * 2**30)]),
        "M_SERVICES": (["HOST", "SERVICE_NAME", "ACTIVE_STATUS"], [(LAB, "nameserver", "YES"), (LAB, "indexserver", "YES")]),
        "M_BACKUP_CATALOG": (["ENTRY_TYPE_NAME", "LAST_END"], [("complete data backup", "2026-11-18 01:00:00")]),
        "M_SERVICE_REPLICATION": (["HOST"], []),
        "STATISTICS_CURRENT_ALERTS": (["ALERT_ID", "ALERT_RATING", "ALERT_DETAILS", "ALERT_TIMESTAMP"], []),
        "M_ACTIVE_STATEMENTS": (["active_count", "long_running", "max_duration_us"], [(4, 1, 45_000_000)]),
        "M_SERVICE_THREADS": (["THREAD_COUNT", "WAITING_THREADS"], [(120, 3)]),
    }

    def __init__(self, log):
        self.log, self.description, self._rows = log, None, []

    def execute(self, sql, params=()):
        self.log.append(sql)
        view = next((v for v in self.ROWS if f" {v}" in sql or f".{v}" in sql), None)
        if view is None:
            raise RuntimeError(f"(259, 'invalid table name: {sql.split('FROM')[-1].split()[0]}')")
        cols, self._rows = self.ROWS[view]
        self.description = [(c, None, None, None, None, None, None) for c in cols]

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


@pytest.fixture
def fake_hdbcli(monkeypatch):
    calls = {"connect": None, "sql": []}

    def connect(**kwargs):
        calls["connect"] = kwargs
        if kwargs["password"] != "Mon-pass-1":
            raise RuntimeError("(10, 'authentication failed')")
        return types.SimpleNamespace(cursor=lambda: _Cursor(calls["sql"]), close=lambda: None)

    module = types.ModuleType("hdbcli")
    module.dbapi = types.SimpleNamespace(connect=connect)
    monkeypatch.setitem(sys.modules, "hdbcli", module)
    return calls


@pytest.fixture
def lab(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "ALLOWED_TARGET_HOSTS", (LAB,))
    monkeypatch.setattr(config, "ALLOWED_TARGET_CIDRS", ("127.0.0.0/8",))
    monkeypatch.setattr(config, "ALLOWED_TARGET_PORTS", (443, 22, 9100))
    simulator.reset_state()
    with TestClient(__import__("app.main", fromlist=["app"]).app) as c:
        system = c.post("/api/v1/systems", headers=KEY, json={"sid": "S4L", "name": "S4L lab", "product": "S4HANA", "stack": "ABAP",
                                                              "database": "HANA", "environment": "DEV"}).json()
        yield c, system


def add(c, system, **body):
    r = c.post(f"/api/v1/systems/{system['id']}/collectors", headers=KEY, json={"interval_seconds": 300, **body})
    assert r.status_code in (200, 201), r.text
    return r.json()


def run(c, collector):
    r = c.post(f"/api/v1/collectors/{collector['id']}/run", headers=KEY)
    assert r.status_code == 200, r.text
    return r.json()


def monitor(c, system, name):
    r = c.get(f"/api/v1/systems/{system['id']}/monitors/{name}", headers=KEY)
    assert r.status_code == 200, r.text
    return r.json()["latest"]


# ---- OS layer ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("user,secret", [("root", {"password": "Root-pass-123"}), ("s4ladm", {"password": "Lab-pass-123"}),
                                         ("s4ladm", {"private_key": "KEY"})])
def test_os_ssh_real_sshd(lab, user, secret):
    c, system = lab
    if secret.get("private_key") == "KEY":
        secret = {"private_key": open("/tmp/labkey").read()}
    col = add(c, system, type="os_ssh", host=LAB, port=22, username=user, **secret)
    out = run(c, col)
    assert out["status"] == "ok", out
    m = out["metrics"]
    # the lab host is both application and database host (SAPDBHOST = saplab): both metric families are emitted
    assert 0 < m["host.memory_used_pct"] < 100 and m["host.load1"] >= 0 and 0 < m["host.disk_used_pct"] <= 100
    assert m["db.os_process_up"] == 1.0 and "db.host_memory_used_pct" in m
    # TOFU: the key is pinned after the first run and a second run still succeeds
    pinned = c.get(f"/api/v1/systems/{system['id']}/collectors", headers=KEY).json()
    assert next(x for x in pinned if x["id"] == col["id"])["host_key"].startswith("SHA256:")
    assert run(c, col)["status"] == "ok"


def test_os_ssh_wrong_password_and_changed_host_key(lab):
    c, system = lab
    bad = run(c, add(c, system, type="os_ssh", host=LAB, port=22, username="root", password="nope"))
    assert bad["status"] == "error" and "authentication" in bad["error"].lower()
    col = add(c, system, type="os_ssh", host=LAB, port=22, username="s4ladm", password=SIDADM[1])
    from app.db import collectors, get_db
    from sqlalchemy import update
    with get_db() as conn:
        conn.execute(update(collectors).where(collectors.c.id == col["id"]).values(host_key="SHA256:not-the-real-key"))
    changed = run(c, col)
    assert changed["status"] == "error" and "changed" in changed["error"]


def test_os_ssh_db_process_down_is_detected(lab):
    c, system = lab
    os.system("pkill -x hdbindexserver; sleep 0.3")
    try:
        out = run(c, add(c, system, type="os_ssh", host=LAB, port=22, username="root", password="Root-pass-123"))
        assert out["metrics"]["db.os_process_up"] == 0.0
        assert any(i["metric"] == "db.os_process_up" for i in (out["incidents"] or {}).get("opened", [])) or out["incidents"]
    finally:
        os.system("nohup /tmp/hdbindexserver 100000 >/dev/null 2>&1 &")


def test_node_exporter_real_binary(lab):
    c, system = lab
    out = run(c, add(c, system, type="node_exporter", host=LAB, port=9100, use_tls=False))
    assert out["status"] == "ok", out
    m = out["metrics"]
    assert {"host.memory_used_pct", "host.disk_used_pct", "host.load1"} <= set(m)
    assert monitor(c, system, "host")


# ---- Application layer -------------------------------------------------------------------------------------------

def test_sapcontrol_real_http(lab, startsrv):
    c, system = lab
    out = run(c, add(c, system, type="sapcontrol", host=LAB, port=50013, use_tls=False, username=SIDADM[0], password=SIDADM[1]))
    assert out["status"] == "ok", out
    m = out["metrics"]
    assert m["availability.system_up"] == 1.0 and m["availability.instances_down"] == 0
    assert m["wp.dia_busy_pct"] == pytest.approx(50.0) and m["wp.btc_busy_pct"] == pytest.approx(100.0) and m["wp.long_running"] >= 1
    assert out["summary"]["availability"]["instances"] == 2
    assert monitor(c, system, "availability") and monitor(c, system, "workprocesses") and monitor(c, system, "syslog")
    calls = {(d["instance"], d["call"]) for d in out["diagnostics"] if d["ok"]}
    assert ("saplab_00", "ABAPGetWPTable") in calls and ("saplab_00", "ABAPReadSyslog") in calls


def test_sapcontrol_wrong_password_is_a_clear_error(lab, startsrv):
    c, system = lab
    out = run(c, add(c, system, type="sapcontrol", host=LAB, port=50013, use_tls=False, username=SIDADM[0], password="wrong"))
    assert out["status"] in ("partial", "error") and "HTTP 401" in (out["error"] or "")


# ---- Database layer ----------------------------------------------------------------------------------------------

def test_hana_sapcontrol_real_http(lab, startsrv):
    c, system = lab
    out = run(c, add(c, system, type="hana_sapcontrol", host=LAB, port=50213, use_tls=False, username="hdbadm", password="x"))
    # GetSystemInstanceList is public; GetProcessList with wrong credentials is reported per instance
    assert out["metrics"]["hana.system_up"] == 1.0
    out = run(c, add(c, system, type="hana_sapcontrol", host=LAB, port=50213, use_tls=False, username=SIDADM[0], password=SIDADM[1]))
    assert out["status"] == "ok" and out["metrics"]["hana.instances_down"] == 0


def test_hana_sql_through_hdbcli_protocol(lab, fake_hdbcli):
    c, system = lab
    out = run(c, add(c, system, type="hana", host=LAB, port=30215, username="MONITOR", password="Mon-pass-1"))
    assert out["status"] in ("ok", "partial"), out
    m = out["metrics"]
    assert m["hana.memory_used_pct"] == pytest.approx(75.0) and m["hana.disk_used_pct"] == pytest.approx(60.0)
    assert m["hana.services_inactive"] == 0
    assert fake_hdbcli["connect"]["address"] == "127.0.0.1" and fake_hdbcli["connect"]["port"] == 30215
    assert monitor(c, system, "hana")
    bad = run(c, add(c, system, type="hana", host=LAB, port=30215, username="MONITOR", password="wrong"))
    assert bad["status"] == "error" and "authentication failed" in bad["error"]
