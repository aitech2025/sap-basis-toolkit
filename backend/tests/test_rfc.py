"""RFC collector: released/ST-PI function modules through the PyRFC client API."""
import sys
import types
from datetime import timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.services.collectors import CollectorError, rfc
from test_collectors import KEY, make_system, setup


class FakeConnection:
    """pyrfc.Connection protocol: Connection(**logon) / call(name, **params) -> dict / close()."""
    calls: list = []
    logons: list = []

    def __init__(self, **params):
        if params["passwd"] != "Rfc-pass-1":
            raise RuntimeError("RFC_LOGON_FAILURE: Name or password is incorrect (repeat logon)")
        FakeConnection.logons.append(params)
        self.xbp = False

    def call(self, name, **params):
        FakeConnection.calls.append((name, params))
        if name == "RFC_SYSTEM_INFO":
            return {"RFCSI_EXPORT": {"RFCSYSID": "S4P", "RFCSAPRL": "758", "RFCDBSYS": "HDB", "RFCHOST": "sapapp1"}}
        if name == "TH_SERVER_LIST":
            return {"LIST": [{"NAME": "sapapp1_S4P_00", "HOST": "sapapp1", "SERV": "sapdp00"}]}
        if name == "/SDF/GET_DUMP_LOG":
            return {"ET_E2E_LOG": [{"C1": "20261118", "C2": "083000", "C3": "TIME_OUT", "C4": "JSMITH", "C5": "sapapp1_S4P_00",
                                    "C6": "100", "C7": "SAPMV45A"}],
                    "ES_E2E_LOG_STRUCT_DESC": {"C1": "Date", "C2": "Time", "C3": "Runtime Error", "C4": "User Name",
                                               "C5": "Application Server", "C6": "Client", "C7": "Terminated Program"}}
        if name == "BAPI_XMI_LOGON":
            self.xbp = True
            return {"SESSIONID": "X1", "RETURN": {"TYPE": "S", "MESSAGE": ""}}
        if name == "BAPI_XMI_LOGOFF":
            self.xbp = False
            return {"RETURN": {"TYPE": "S"}}
        if name == "BAPI_XBP_JOB_SELECT":
            assert self.xbp, "XBP calls need an XMI session"
            return {"SELECTED_JOBS": [{"JOBNAME": "Z_FI_NIGHTLY", "JOBCOUNT": "10000001", "STATUS": "A", "SDLUNAME": "BATCH_FI",
                                       "STRTDATE": "20261118", "STRTTIME": "010000", "ENDDATE": "20261118", "ENDTIME": "010500"}],
                    "RETURN": {"TYPE": "S"}}
        raise RuntimeError(f"FU_NOT_FOUND: {name}")

    def close(self):
        pass


@pytest.fixture
def fake_pyrfc(monkeypatch):
    FakeConnection.calls, FakeConnection.logons = [], []
    module = types.ModuleType("pyrfc")
    module.Connection = FakeConnection
    monkeypatch.setitem(sys.modules, "pyrfc", module)
    return FakeConnection


def test_dump_columns_are_mapped_from_the_description_structure():
    desc = {"C1": "Date", "C2": "Time", "C3": "Runtime Error", "C4": "User Name", "C5": "Application Server", "C6": "Client"}
    cols = rfc.map_dump_columns(desc, {k: "" for k in desc})
    assert cols == {"date": "C1", "time": "C2", "error": "C3", "user": "C4", "host": "C5", "client": "C6"}
    # semantic column names win when the table has them
    assert rfc.map_dump_columns({}, {"DATUM": "", "UZEIT": "", "ERRID": "", "UNAME": ""})["error"] == "ERRID"


def test_client_logs_on_with_policy_resolved_address_and_collects(monkeypatch, fake_pyrfc):
    monkeypatch.setattr(config, "ALLOWED_TARGET_HOSTS", ("localhost",))
    monkeypatch.setattr(config, "ALLOWED_TARGET_CIDRS", ("127.0.0.0/8", "::1/128"))  # CI resolves localhost to both
    client = rfc.RfcClient("localhost", 3300, "100", "SENTINEL_RFC", "Rfc-pass-1")
    assert fake_pyrfc.logons[0]["ashost"] in ("127.0.0.1", "::1") and fake_pyrfc.logons[0]["sysnr"] == "00"
    out = rfc.collect(client, ZoneInfo("Europe/Berlin"))
    assert out["system"]["sid"] == "S4P" and out["servers"][0]["host"] == "sapapp1"
    dump = out["dumps"][0]
    assert dump["error"] == "TIME_OUT" and dump["program"] == "SAPMV45A" and dump["at"].hour == 7  # 08:30 Berlin = 07:30 UTC
    assert out["jobs"][0]["STATUS"] == "A"
    names = [c[0] for c in fake_pyrfc.calls]
    assert names.index("BAPI_XMI_LOGON") < names.index("BAPI_XBP_JOB_SELECT") < names.index("BAPI_XMI_LOGOFF")
    select = dict(fake_pyrfc.calls)["BAPI_XBP_JOB_SELECT"]
    assert select["SELECTION"] == "AL" and select["JOB_SELECT_PARAM"]["ABORTED"] == "X"
    assert all(d["ok"] for d in out["diagnostics"])
    with pytest.raises(CollectorError, match="RFC logon failed"):
        rfc.RfcClient("localhost", 3300, "100", "SENTINEL_RFC", "wrong")
    with pytest.raises(CollectorError, match="SAP client"):
        rfc.RfcClient("localhost", 3300, "1", "SENTINEL_RFC", "Rfc-pass-1")


def test_missing_rfc_library_is_a_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyrfc", None)
    with pytest.raises(CollectorError, match="No RFC library"):
        rfc.RfcClient("localhost", 3300, "100", "U", "P")


def test_rfc_collector_end_to_end_with_simulator(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with TestClient(app) as c:
        system = make_system(c)
        bad = c.post(f"/api/v1/systems/{system['id']}/collectors", headers=KEY, json={
            "type": "rfc", "host": "sapapp1.corp.example.test", "port": 50013, "username": "X", "sap_client": "100"})
        assert bad.status_code == 422 and "33<NN>" in bad.text
        col = c.post(f"/api/v1/systems/{system['id']}/collectors", headers=KEY, json={
            "type": "rfc", "host": "simulator-s4p", "port": 3300, "username": "SENTINEL_RFC", "password": "pw",
            "sap_client": "100", "interval_seconds": 300}).json()
        assert col["sap_client"] == "100"
        out = c.post(f"/api/v1/collectors/{col['id']}/run", headers=KEY).json()
        assert out["status"] == "ok", out
        assert out["metrics"]["rfc.app_servers"] == 2 and "dumps.last_24h" in out["metrics"]
        assert {d["call"] for d in out["diagnostics"]} == {"RFC_SYSTEM_INFO", "TH_SERVER_LIST", "/SDF/GET_DUMP_LOG", "BAPI_XBP_JOB_SELECT"}
        dumps = c.get(f"/api/v1/systems/{system['id']}/monitors/dumps", headers=KEY).json()
        assert dumps["latest"]["source"] == "simulator"
        jobs = c.get(f"/api/v1/systems/{system['id']}/jobs/report?days=7", headers=KEY).json()
        assert jobs["summary"]["runs"] > 0
