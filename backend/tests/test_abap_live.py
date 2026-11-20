"""Near-real-time ABAP signals through sapstartsrv: SM12 lock table, enqueue statistics, dispatcher queues and CCMS."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.main import app
from app.services import monitors
from app.services.collectors import sapcontrol
from test_collectors import KEY, make_system, setup

ENV = ('<?xml version="1.0" encoding="UTF-8"?><SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" '
       'xmlns:SAPControl="urn:SAPControl"><SOAP-ENV:Body><SAPControl:{m}Response>{b}</SAPControl:{m}Response></SOAP-ENV:Body>'
       '</SOAP-ENV:Envelope>')


def test_lock_table_parsing_uses_owner_time_in_system_timezone():
    berlin = ZoneInfo("Europe/Berlin")
    now = datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc)
    xml = ENV.format(m="EnqGetLockTable", b=(
        "<lock><item><lock-name>VBAK</lock-name><lock-arg>1000000004711</lock-arg><lock-mode>E</lock-mode>"
        "<owner>20261118080000123456000sapapp1_S4P_00</owner><owner-vb></owner-vb><use-count-owner>1</use-count-owner>"
        "<use-count-owner-vb>0</use-count-owner-vb><client>100</client><user>JSMITH</user><transaction>VA02</transaction>"
        "<object></object><backup>FALSE</backup></item>"
        "<item><lock-name>MARA</lock-name><lock-arg>100X</lock-arg><lock-mode>S</lock-mode><owner>NOTIMESTAMP</owner>"
        "<client>100</client><user>RFC_PI</user></item></lock>"))
    locks = sapcontrol.parse_locks(sapcontrol.parse_items(xml), berlin, now)
    # 08:00 Berlin (UTC+1 in November) = 07:00 UTC -> 120 minutes before 09:00 UTC
    assert locks[0]["table_name"] == "VBAK" and locks[0]["user_name"] == "JSMITH" and locks[0]["age_minutes"] == 120
    assert locks[0]["transaction"] == "VA02" and locks[0]["lock_mode"] == "E" and locks[0]["backup"] is False
    assert locks[1]["locked_at"] is None and locks[1]["age_minutes"] is None  # never invent an age


def test_enqueue_statistic_queue_and_ccms_parsing():
    stat = sapcontrol.parse_enqueue_statistic(sapcontrol.parse_fields(ENV.format(m="EnqGetStatistic", b=(
        "<owner-now>10</owner-now><owner-max>1000</owner-max><arguments-now>50</arguments-now><arguments-max>1000</arguments-max>"
        "<locks-now>750</locks-now><locks-high>800</locks-high><locks-max>1000</locks-max><enqueue-rejects>3</enqueue-rejects>"))))
    assert stat["table_used_pct"] == 75.0 and stat["locks_now"] == 750 and stat["enqueue_rejects"] == 3
    queues = sapcontrol.parse_queues(sapcontrol.parse_items(ENV.format(m="GetQueueStatistic", b=(
        "<queue><item><Typ>ABAP/DIA</Typ><Now>12</Now><High>40</High><Max>14000</Max><Writes>1</Writes><Reads>1</Reads></item>"
        "<item><Typ>ABAP/UPD</Typ><Now>0</Now><High>3</High><Max>14000</Max></item></queue>"))))
    assert queues[0] == {"type": "ABAP/DIA", "now": 12, "high": 40, "max": 14000}
    tree = sapcontrol.parse_alert_tree(sapcontrol.parse_items(ENV.format(m="GetAlertTree", b=(
        "<tree><item><name>sapapp1_S4P_00</name><parent>-1</parent><ActualValue>SAPControl-YELLOW</ActualValue><description></description></item>"
        "<item><name>R3Services</name><parent>0</parent><ActualValue>SAPControl-YELLOW</ActualValue><description></description></item>"
        "<item><name>Dialog</name><parent>1</parent><ActualValue>SAPControl-YELLOW</ActualValue><description></description></item>"
        "<item><name>ResponseTime</name><parent>2</parent><ActualValue>SAPControl-YELLOW</ActualValue>"
        "<description>1834 msec</description><Time>2026 11 18 09:00:00</Time></item></tree>"))))
    leaf = next(n for n in tree if n["leaf"])
    assert leaf["path"] == "sapapp1_S4P_00\\R3Services\\Dialog\\ResponseTime" and leaf["value"] == 1834.0 and leaf["color"] == "YELLOW"


def test_build_abap_live_metrics_and_health():
    now = datetime.now(timezone.utc)
    live = [{"instance": "app1_00", "queues": [{"type": "ABAP/DIA", "now": 12, "high": 40, "max": 14000}],
             "ccms_tree": [{"name": "ResponseTime", "path": "x", "color": "YELLOW", "value": 1800.0, "leaf": True, "description": "", "time": ""},
                           {"name": "Shortdumps", "path": "y", "color": "RED", "value": None, "leaf": True, "description": "", "time": ""}],
             "ccms_alerts": [{"object": "Shortdumps", "attribute": "Shortdumps", "color": "RED", "description": "old", "time": "2026 11 01 10:00:00"}]},
            {"instance": "app2_01", "queues": [{"type": "ABAP/DIA", "now": 3, "high": 9, "max": 14000}],
             "ccms_tree": [{"name": "ResponseTime", "path": "z", "color": "GREEN", "value": 600.0, "leaf": True, "description": "", "time": ""}]},
            {"instance": "ascs_10", "enqueue": {"table_used_pct": 75.0}, "locks": [
                {"user_name": "A", "table_name": "T", "argument": "1", "age_minutes": 130, "locked_at": now},
                {"user_name": "B", "table_name": "T", "argument": "2", "age_minutes": None, "locked_at": None}]}]
    data, m, health = monitors.build_abap_live(live, now)
    assert m["abap.queue_dia"] == 15 and m["abap.dialog_response_ms"] == 1200.0
    assert m["ccms.red_now"] == 1 and m["ccms.yellow_now"] == 1 and m["abap.enq_table_used_pct"] == 75.0
    assert m["abap.locks"] == 2 and m["abap.locks_oldest_min"] == 130 and m["abap.locks_long"] == 1
    assert health == "critical" and data["summary"]["open_alerts"] == 1  # open alerts are listed, not counted as current


def test_sapcontrol_collector_feeds_live_monitor_and_lock_report(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with TestClient(app) as c:
        system = make_system(c)
        col = c.post(f"/api/v1/systems/{system['id']}/collectors", headers=KEY, json={
            "type": "sapcontrol", "host": "simulator-s4p", "port": 50013, "use_tls": False, "username": "s4padm",
            "password": "pw", "interval_seconds": 60}).json()
        out = c.post(f"/api/v1/collectors/{col['id']}/run", headers=KEY).json()
        assert out["status"] == "ok", out
        m = out["metrics"]
        assert {"abap.queue_dia", "abap.dialog_response_ms", "abap.users_logged_on", "abap.enq_table_used_pct",
                "abap.locks", "ccms.red_now"} <= set(m)
        calls = {d["call"] for d in out["diagnostics"] if d["ok"]}
        assert {"GetQueueStatistic", "GetAlertTree", "GetAlerts", "EnqGetLockTable", "EnqGetStatistic"} <= calls
        live = c.get(f"/api/v1/systems/{system['id']}/monitors/abap_live", headers=KEY).json()
        assert live["latest"]["data"]["summary"]["locks"] == m["abap.locks"]
        report = c.get(f"/api/v1/systems/{system['id']}/locks/report", headers=KEY).json()
        assert report["latest"] and report["summary"]["locks"] == m["abap.locks"]
        # a second run a minute later updates metrics but does not write another lock snapshot (every 5 minutes)
        again = c.post(f"/api/v1/collectors/{col['id']}/run", headers=KEY).json()
        assert "locks_snapshot" not in again["summary"]
        catalogue = {x["key"] for x in c.get("/api/v1/analytics/metrics", headers=KEY).json()}
        assert {k for k in m if k != "collector.failed"} <= catalogue
