"""Basis daily checks: new pulled/pushed signals and the fleet daily-check matrix."""
from datetime import timezone

from fastapi.testclient import TestClient

from app.main import app
from app.services import monitors
from app.services.collectors import node_exporter, ssh
from test_collectors import KEY, make_system, setup


def test_cpu_and_swap_from_proc_and_node_exporter():
    assert ssh.cpu_busy_pct(["cpu  100 0 100 800 0 0 0 0", "cpu  160 0 140 900 0 0 0 0"]) == 50.0
    assert ssh.cpu_busy_pct(["cpu  1 2 3"]) is None
    payload = ("node_cpu_seconds_total{cpu=\"0\",mode=\"idle\"} 900\nnode_cpu_seconds_total{cpu=\"0\",mode=\"user\"} 100\n"
               "node_memory_SwapTotal_bytes 1000\nnode_memory_SwapFree_bytes 750\n")
    first = node_exporter.parse_metrics(payload)
    assert first["host.swap_used_pct"] == 25.0
    assert node_exporter.cpu_from_counters("h:9100", first.pop("_cpu_counters")) is None  # needs two scrapes
    second = node_exporter.parse_metrics(payload.replace("} 900", "} 930").replace("} 100", "} 170"))
    assert node_exporter.cpu_from_counters("h:9100", second.pop("_cpu_counters")) == 70.0


def test_number_ranges_security_and_buffers_builders():
    data, m, health = monitors.build_number_ranges([
        {"CLIENT": "100", "OBJECT": "RV_BELEG", "FROMNUMBER": "0090000000", "TONUMBER": "0099999999", "NRLEVEL": "0099100000"},
        {"CLIENT": "100", "OBJECT": "ALPHA", "FROMNUMBER": "A000", "TONUMBER": "Z999", "NRLEVEL": "B000"}])
    assert m["numrange.max_used_pct"] == 91.0 and health == "critical" and data["ranges"][0]["object"] == "RV_BELEG"
    sec = {"clients": [{"mandt": "000", "cccoractiv": "2", "ccnocliind": "3"}, {"mandt": "100", "cccoractiv": "1", "ccnocliind": "3"}],
           "critical_users": [{"mandt": "100", "bname": "SAP*", "uflag": 0}, {"mandt": "000", "bname": "SAP*", "uflag": 64}],
           "failed_logon_locks": [{"mandt": "100", "locked": 3}]}
    data, m, health = monitors.build_security(sec, "PRD")
    assert m["security.prod_clients_open"] == 1 and m["security.sapstar_risk"] == 1 and m["security.users_locked_failed_logon"] == 3
    assert health == "warning" and {f["check"] for f in data["findings"]} >= {"Client open for changes (SCC4)", "SAP* not locked (SU01)"}
    _, m_dev, _ = monitors.build_security(sec, "DEV")
    assert "security.prod_clients_open" not in m_dev  # open clients are normal in development
    tree = [{"instance": "a_00", "ccms_tree": [
        {"name": "HitRatio", "path": "ctx\\Buffers\\Program\\HitRatio", "value": 97.2, "color": "GREEN", "leaf": True, "description": "", "time": ""},
        {"name": "Swap", "path": "ctx\\Buffers\\Program\\Swap", "value": 2500.0, "color": "YELLOW", "leaf": True, "description": "", "time": ""}]}]
    _, m, _ = monitors.build_abap_live(tree)
    assert m["abap.buffer_hitratio_min"] == 97.2 and m["abap.buffer_swaps"] == 2500.0


def test_simulator_pulls_new_signals(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with TestClient(app) as c:
        system = make_system(c)
        out = {}
        for body in ({"type": "sapcontrol", "host": "simulator-s4p", "port": 50013, "use_tls": False, "username": "a", "password": "b"},
                     {"type": "hana", "host": "simulator-s4p", "port": 30015, "abap_schema": "SAPHANADB"},
                     {"type": "os_ssh", "host": "simulator-s4p", "port": 22, "username": "root", "password": "pw"}):
            col = c.post(f"/api/v1/systems/{system['id']}/collectors", headers=KEY, json={**body, "interval_seconds": 300}).json()
            run = c.post(f"/api/v1/collectors/{col['id']}/run", headers=KEY).json()
            assert run["status"] in ("ok", "partial"), run
            out.update(run["metrics"])
        expected = {"abap.buffer_hitratio_min", "abap.buffer_swaps", "numrange.max_used_pct", "applog.error_logs_24h",
                    "batchinput.error_sessions_7d", "spool.requests", "hana.log_disk_used_pct", "hana.blocked_transactions",
                    "hana.oom_events_24h", "host.cpu_pct", "host.swap_used_pct", "security.prod_clients_open"}
        assert expected <= set(out), expected - set(out)


def test_daily_check_matrix_csv_and_review(monkeypatch, tmp_path):
    from datetime import datetime, timedelta, timezone as tz
    from app.services import monitors as mon
    setup(monkeypatch, tmp_path)
    with TestClient(app) as c:
        prd = make_system(c, sid="S4P")
        dev = c.post("/api/v1/systems", headers=KEY, json={"sid": "S4D", "name": "S4D", "product": "S4HANA", "stack": "ABAP",
                                                            "database": "HANA", "environment": "DEV"}).json()
        now = datetime.now(tz.utc)
        mon.record_metrics(prd["id"], {"availability.system_up": 1.0, "numrange.max_used_pct": 93.0, "host.cpu_pct": 40.0,
                                       "jobs.cancelled_24h": 2.0})
        mon.record_metrics(prd["id"], {"host.disk_used_pct": 50.0}, now - timedelta(hours=3))  # stale
        mon.record_metrics(dev["id"], {"availability.system_up": 1.0, "host.cpu_pct": 30.0})
        # an older value arriving late never replaces the latest one
        mon.record_metrics(dev["id"], {"host.cpu_pct": 99.0}, now - timedelta(hours=1))
        data = c.get("/api/v1/daily-checks", headers=KEY).json()
        by_sid = {s["sid"]: s for s in data["systems"]}
        p = by_sid["S4P"]["cells"]
        assert p["numrange.max_used_pct"]["status"] == "critical" and p["numrange.max_used_pct"]["threshold"]["source"] == "rule"
        assert p["host.disk_used_pct"]["status"] == "stale" and p["jobs.cancelled_24h"]["status"] == "warning"
        assert by_sid["S4P"]["overall"] == "critical" and data["systems"][0]["sid"] == "S4P"  # worst first
        assert by_sid["S4D"]["cells"]["host.cpu_pct"]["value"] == 30.0 and by_sid["S4D"]["overall"] == "ok"
        assert {"Number range used", "App host CPU"} <= {chk["label"] for chk in data["checks"]}
        assert c.get("/api/v1/daily-checks?environment=DEV", headers=KEY).json()["summary"]["systems"] == 1
        csv_text = c.get("/api/v1/daily-checks/export.csv", headers=KEY).text
        assert csv_text.splitlines()[0].startswith("SID,Landscape,Environment,Overall") and "critical: 93%" in csv_text and "ok: up" in csv_text
        r = c.post("/api/v1/daily-checks/reviews", headers=KEY, json={"comment": "RV_BELEG range extended in SNRO"})
        assert r.status_code == 201 and r.json()["summary"]["critical"] == 1
        listed = c.get("/api/v1/daily-checks/reviews", headers=KEY).json()
        assert listed[0]["comment"].startswith("RV_BELEG") and "results" not in listed[0]
        detail = c.get(f"/api/v1/daily-checks/reviews/{listed[0]['id']}", headers=KEY).json()
        assert detail["results"]["summary"]["systems"] == 2


def test_sap_dates_keep_their_zeros():
    assert monitors._sap_date("20261110") == "2026-11-10" and monitors._sap_date("00000000") is None
    data, _, _ = monitors.build_security({"critical_users": [{"mandt": "100", "bname": "DDIC", "uflag": 0, "trdat": "20261110"}]}, "DEV")
    assert data["standard_users"][0]["last_logon"] == "2026-11-10"
