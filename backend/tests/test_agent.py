"""ABAP push agent: token lifecycle, ingest of the JSON that ZCL_SENTINEL_AGENT sends, and the silence watchdog."""
import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import update

from app import config
from app.db import agent_tokens, get_db
from app.main import app
from app.services import agent
from test_collectors import KEY, make_system, setup


def payload(sid="S4P", **over):
    now = datetime.now(timezone.utc)
    hour_ago = now - timedelta(hours=1)
    d, t = hour_ago.strftime("%Y%m%d"), hour_ago.strftime("%H%M%S")
    log = {"ET_E2E_LOG": [{"FIELD1": d, "FIELD2": t, "FIELD3": "TIME_OUT", "FIELD4": "JSMITH", "FIELD5": "sapapp1_S4P_00",
                           "FIELD6": "100", "FIELD7": "SAPMV45A"}],
           "ES_E2E_LOG_STRUCT_DESC": {"FIELD1": "Date", "FIELD2": "Time", "FIELD3": "Runtime Error", "FIELD4": "User",
                                      "FIELD5": "Application Server", "FIELD6": "Client", "FIELD7": "Program"}}
    body = {
        "schema": 1, "agent_version": "1.0.0", "sid": sid, "client": "100", "host": "sapapp1", "release": "758",
        "dbsys": "HDB", "timezone": "UTC", "sent_at": now.strftime("%Y%m%d%H%M%S"), "window_hours": 24,
        "dump_source": "/SDF/GET_DUMP_LOG", "dump_log": json.dumps(log), "dumps": [],
        "jobs": [{"jobname": "Z_FI_NIGHTLY", "jobcount": "10000001", "sdluname": "BATCH_FI", "status": "A",
                  "strtdate": d, "strttime": t, "enddate": d, "endtime": t}],
        "updates": [{"vbkey": "ABC", "vbusr": "JSMITH", "vbtcode": "VA01", "vbreport": "SAPMV45A", "vbdate": d + t, "vbrc": "12"}],
        "locks": [{"gname": "VBAK", "garg": "1000000004711", "gmode": "E", "guname": "JSMITH", "gclient": "100",
                   "gtdate": (now - timedelta(minutes=95)).strftime("%Y%m%d"), "gttime": (now - timedelta(minutes=95)).strftime("%H%M%S"),
                   "gtcode": "VA02"}],
        "trfc": [{"arfcdest": "PI_PROD", "arfcstate": "SYSFAIL", "arfcfnam": "IDOC_INBOUND_ASYNCHRONOUS", "arfcuser": "RFC_PI",
                  "arfcdatum": d, "arfcuzeit": t}],
        "qrfc_out": [{"qname": "CRM_SITE_01", "dest": "CRM", "qstate": "SYSFAIL", "entries": 42}], "qrfc_in": [],
        "idoc_errors": [{"docnum": "0000000000123456", "status": "51", "mestyp": "ORDERS", "direct": "2", "credat": d,
                         "cretim": t, "sndprn": "CUST01", "rcvprn": "S4PCLNT100"}],
        "idoc_total": 120, "errors": ["qrfc: no authorisation for TRFCQIN"],
    }
    body.update(over)
    return body


def test_token_lifecycle_and_ingest(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "AGENT_MIN_INTERVAL_SECONDS", 0)
    with TestClient(app) as c:
        system = make_system(c)
        issued = c.post(f"/api/v1/systems/{system['id']}/agent/token", headers=KEY)
        assert issued.status_code == 201
        token = issued.json()["token"]
        assert token.startswith("sat_") and issued.json()["hint"] == token[:10]
        status = c.get(f"/api/v1/systems/{system['id']}/agent", headers=KEY).json()
        assert status["active"]["hint"] == token[:10] and "token" not in status["active"]

        # wrong / missing token, API key is not accepted on the ingest endpoint
        assert c.post("/api/v1/agent/ingest", json=payload()).status_code == 401
        assert c.post("/api/v1/agent/ingest", json=payload(), headers=KEY).status_code == 401
        assert c.post("/api/v1/agent/ingest", json=payload(), headers={"X-Sentinel-Agent-Token": "sat_nope"}).status_code == 401
        hdr = {"X-Sentinel-Agent-Token": token}
        assert c.post("/api/v1/agent/ingest", json=payload(sid="ECP"), headers=hdr).status_code == 403
        assert c.post("/api/v1/agent/ingest", json=payload(schema=2), headers=hdr).status_code == 422

        r = c.post("/api/v1/agent/ingest", json=payload(), headers=hdr)
        assert r.status_code == 200, r.text
        out = r.json()
        assert set(out["areas"]) >= {"dumps", "updates", "trfc", "qrfc", "idocs", "jobs", "locks"}
        assert out["not_collected_in_sap"] == ["qrfc: no authorisation for TRFCQIN"]

        def latest(mon):
            return c.get(f"/api/v1/systems/{system['id']}/monitors/{mon}", headers=KEY).json()["latest"]
        dumps = latest("dumps")
        assert dumps["source"] == "abap_agent" and dumps["data"]["summary"]["last_24h"] == 1
        assert dumps["data"]["recent"][0]["error"] == "TIME_OUT"
        assert latest("updates")["data"]["summary"]["errors"] == 1
        assert latest("trfc")["data"]["summary"]["errors"] == 1
        assert latest("qrfc")["data"]["summary"]["blocked_queues"] == 1
        assert latest("idocs")["data"]["summary"]["errors_24h"] == 1
        jobs = c.get(f"/api/v1/systems/{system['id']}/jobs/report?days=1", headers=KEY).json()
        assert jobs["summary"]["cancelled"] == 1
        locks = c.get(f"/api/v1/systems/{system['id']}/locks/report", headers=KEY).json()
        assert locks["summary"]["locks"] == 1 and locks["long_running"][0]["table"] == "VBAK"
        assert 94 <= locks["long_running"][0]["age_minutes"] <= 96

        # the push is not written to the audit log (tracked on the token instead)
        audit = c.get("/api/v1/audit", headers=KEY).json()
        assert not any("agent/ingest" in (a.get("action") or "") for a in audit)
        seen = c.get(f"/api/v1/systems/{system['id']}/agent", headers=KEY).json()["active"]
        assert seen["last_seen_at"] and seen["last_agent_version"] == "1.0.0"

        # rotating revokes the old token
        new = c.post(f"/api/v1/systems/{system['id']}/agent/token", headers=KEY).json()["token"]
        assert c.post("/api/v1/agent/ingest", json=payload(), headers=hdr).status_code == 401
        assert c.post("/api/v1/agent/ingest", json=payload(), headers={"X-Sentinel-Agent-Token": new}).status_code == 200


def test_snap_fallback_rate_limit_size_limit_and_watchdog(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with TestClient(app) as c:
        system = make_system(c)
        token = c.post(f"/api/v1/systems/{system['id']}/agent/token", headers=KEY).json()["token"]
        hdr = {"X-Sentinel-Agent-Token": token}
        now = datetime.now(timezone.utc)
        snap = payload(dump_source="SNAP", dump_log="", dumps=[{"datum": now.strftime("%Y%m%d"), "uzeit": "000001",
                                                                "ahost": "sapapp1", "uname": "X", "mandt": "100"}])
        assert c.post("/api/v1/agent/ingest", json=snap, headers=hdr).status_code == 200
        dumps = c.get(f"/api/v1/systems/{system['id']}/monitors/dumps", headers=KEY).json()["latest"]["data"]
        assert "no ST-PI" in dumps["recent"][0]["error"]
        assert c.post("/api/v1/agent/ingest", json=payload(), headers=hdr).status_code == 429  # within 20 s
        monkeypatch.setattr(config, "AGENT_MIN_INTERVAL_SECONDS", 0)
        monkeypatch.setattr(config, "AGENT_MAX_BYTES", 1000)
        assert c.post("/api/v1/agent/ingest", json=payload(), headers=hdr).status_code == 413
        # silence: last push 20 minutes ago -> agent.up = 0 -> incident
        with get_db() as conn:
            conn.execute(update(agent_tokens).values(last_seen_at=now - timedelta(minutes=20)))
        assert agent.watchdog() == [system["id"]]
        incidents = c.get(f"/api/v1/incidents?system_id={system['id']}", headers=KEY).json()
        assert any(i["metric"] == "agent.up" for i in incidents)


def test_operator_cannot_issue_tokens(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    from app import auth
    with TestClient(app) as c:
        system = make_system(c)
        auth.create_user("op3", "Operator-pass-123", "operator")
        tok = c.post("/api/v1/auth/login", json={"username": "op3", "password": "Operator-pass-123"}).json()["token"]
        r = c.post(f"/api/v1/systems/{system['id']}/agent/token", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 403
