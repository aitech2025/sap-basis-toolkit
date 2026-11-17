from datetime import date, datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from app import config, db
from app.main import app
from app.services import alerts, cert_import, growth, notifications, transports, users

HEADERS = {"X-API-Key": "test-secret"}


def setup_test_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(config, "API_AUTH_TOKEN", "test-secret")
    monkeypatch.setattr(config, "ALLOWED_TARGET_HOSTS", ("qa.example.test",))
    monkeypatch.setattr(config, "ALLOWED_TARGET_PORTS", (443,))
    db.reset_engine_for_tests()


def capture_alerts(monkeypatch):
    sent = []
    monkeypatch.setattr(config, "ALERT_WEBHOOK_URL", "https://hooks.example.test")
    monkeypatch.setattr(notifications, "send_webhook", lambda payload: (sent.append(payload) or True, None))
    return sent


def make_cert(days_valid: int, cn: str = "sapprd.example.test", key_pem: bool = False) -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=days_valid, hours=1))
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM)
    if key_pem:
        pem += key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                 serialization.NoEncryption())
    return pem


# ------------------------------------------------------------------ notifications
def test_slack_teams_and_pagerduty_formats(monkeypatch):
    payload = {"event": "certificate.monitoring", "target_name": "PRD ICM", "status": "critical",
               "days_remaining": 5, "hostname": "sapprd.example.test"}
    slack = notifications.format_webhook_payload(payload, "slack")
    assert "PRD ICM" in slack["text"] and slack["blocks"]
    teams = notifications.format_webhook_payload(payload, "teams")
    card = teams["attachments"][0]
    assert card["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert any(f["title"] == "hostname" for f in card["content"]["body"][1]["facts"])
    assert notifications.format_webhook_payload(payload, "generic") == payload
    monkeypatch.setattr(config, "PAGERDUTY_ROUTING_KEY", "rk")
    event = notifications.pagerduty_event(payload, "dedup-1", "critical")
    assert event["event_action"] == "trigger" and event["payload"]["severity"] == "critical"
    assert event["payload"]["source"] == "sapprd.example.test"


def test_generic_alerts_are_deduplicated(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    db.init_db()
    sent = capture_alerts(monkeypatch)
    alerts.send_alert_once("test", "fp-1", "first", "warning")
    second = alerts.send_alert_once("test", "fp-1", "first again", "warning")
    assert len(sent) == 1 and second[0]["result"] == "suppressed"


# ------------------------------------------------------------------ certificates
def test_enable_target_and_summary(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    with TestClient(app) as client:
        target = client.post("/api/v1/targets", json={"name": "QA", "hostname": "qa.example.test"}, headers=HEADERS).json()
        client.patch(f"/api/v1/targets/{target['id']}/disable", headers=HEADERS)
        enabled = client.patch(f"/api/v1/targets/{target['id']}/enable", headers=HEADERS)
        assert enabled.json()["enabled"] is True
        summary = client.get("/api/v1/certificates/summary", headers=HEADERS).json()
        assert summary["counts"] == {"pending": 1}


def test_strust_import_deduplicates_and_alerts(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    sent = capture_alerts(monkeypatch)
    bundle = make_cert(5, "expiring.example.test") + make_cert(400, "healthy.example.test")
    with TestClient(app) as client:
        url = "/api/v1/certificates/import?sap_system_id=prd&pse_name=SSL%20client%20Standard"
        first = client.post(url, content=bundle, headers=HEADERS)
        assert first.status_code == 201 and first.json()["imported"] == 2
        again = client.post(url, content=bundle, headers=HEADERS).json()
        assert again["imported"] == 0 and again["duplicates_skipped"] == 2
        listed = client.get("/api/v1/certificates/imported?sap_system_id=PRD", headers=HEADERS).json()
        assert [c["status"] for c in listed] == ["critical", "healthy"]
        summary = client.get("/api/v1/certificates/summary", headers=HEADERS).json()
        assert summary["counts"]["critical"] == 1
    assert cert_import.evaluate_imported_certificates() == 1
    cert_import.evaluate_imported_certificates()
    assert len(sent) == 1 and "PRD" in sent[0]["summary"]


def test_strust_import_rejects_private_keys(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/v1/certificates/import?sap_system_id=PRD&pse_name=x",
                               content=make_cert(30, key_pem=True), headers=HEADERS)
    assert response.status_code == 422 and "Private key" in response.json()["detail"]


# ------------------------------------------------------------------ table growth
def test_growth_report_forecast_and_alert(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    sent = capture_alerts(monkeypatch)
    now = datetime.now(timezone.utc)
    with TestClient(app) as client:
        for i, day in enumerate((20, 10, 0)):
            at = (now - timedelta(days=day)).isoformat()
            # semicolon-delimited, German decimal comma, HANA-style bytes column for one table
            csv = (
                "TABLE_NAME;SIZE_MB;RECORD_COUNT\n"
                f"BALDAT;{1000 + i * 500},0;{1000000 + i * 10}\n"
                f"MARA;{200 + i};5000\n"
            )
            r = client.post("/api/v1/growth/HDB/snapshots", params={"captured_at": at}, content=csv, headers=HEADERS)
            assert r.status_code == 201, r.text
            client.post("/api/v1/growth/HDB/capacity", headers=HEADERS, json={
                "disk_total_gb": 100, "disk_used_gb": 80 + i * 4, "captured_at": at,
            })
        report = client.get("/api/v1/growth/HDB/report", headers=HEADERS).json()
    top = report["top_growing_tables"][0]
    assert top["table_name"] == "BALDAT" and top["growth_mb_per_day"] == 50.0
    assert "SBAL_DELETE" in top["recommendation"]
    assert report["housekeeping_candidates"][0]["table_name"] == "BALDAT"
    forecast = report["capacity_forecast"]
    # 88 GB used, 90 GB threshold, growing 0.4 GB/day -> 5 days
    assert forecast["disk_used_pct"] == 88.0 and forecast["days_until_disk_threshold"] == 5.0
    raised = growth.evaluate_growth_alerts()
    growth.evaluate_growth_alerts()
    assert raised[0]["severity"] == "critical" and len(sent) == 1


def test_growth_snapshot_accepts_hana_bytes_and_aggregates_partitions():
    raw = b"SCHEMA_NAME,TABLE_NAME,MEMORY_SIZE_IN_TOTAL\nSAPHANADB,EDIDC,1048576\nSAPHANADB,EDIDC,2097152\n"
    parsed = growth.parse_snapshot(raw)
    assert parsed == [{"table_name": "EDIDC", "size_mb": 3.0, "row_count": None}]


def test_growth_rejects_unusable_upload(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.post("/api/v1/growth/HDB/snapshots", content=b"foo,bar\n1,2\n", headers=HEADERS)
    assert r.status_code == 422 and "table name" in r.json()["detail"]


# ------------------------------------------------------------------ users & licenses
USR02 = """BNAME\tUSTYP\tUFLAG\tTRDAT\tERDAT\tGLTGB\tLIC_TYPE
DDIC\tA\t0\t00000000\t20100101\t00000000\tProfessional
JSMITH\tA\t0\t20260101\t20200101\t00000000\tProfessional
NEWHIRE\tA\t0\t00000000\t20261110\t00000000\tProfessional
GHOST\tA\t0\t00000000\t20240101\t00000000\tProfessional
LOCKED1\tA\t64\t20230101\t20200101\t00000000\tProfessional
RFC_USER\tB\t0\t20200101\t20200101\t00000000\t
VIEWER\tA\t0\t20261115\t20200101\t00000000\tProfessional
ESSUSER\tA\t0\t20261116\t20200101\t00000000\tLimited
POWER\tA\t0\t20261116\t20200101\t00000000\tProfessional
CONTRACT\tA\t0\t20261116\t20200101\t20261001\tProfessional
"""
USAGE = """User;Transaction;Dialog Steps
VIEWER;VA03;120
VIEWER;ME23N;40
VIEWER;S_ALR_87012284;3
ESSUSER;SU3;2
ESSUSER;CAT2;30
POWER;VA01;300
POWER;VA03;20
"""


def test_user_license_analysis():
    report = users.analyse(USR02.encode(), USAGE.encode(), today=date(2026, 11, 17))
    locks = {u["user"]: u["reason"] for u in report["lock_candidates"]}
    assert set(locks) == {"JSMITH", "GHOST", "CONTRACT"}
    assert "inactive for 320 days" in locks["JSMITH"] and "validity expired" == locks["CONTRACT"]
    changes = {c["user"]: c for c in report["license_changes"]}
    assert changes["VIEWER"]["recommended_license"] == "Limited (Core)"
    assert changes["ESSUSER"]["recommended_license"] == "Employee (Self-Service)"
    assert set(changes) == {"VIEWER", "ESSUSER"}  # no recommendation without usage data or for protected users
    summary = report["summary"]
    assert summary["excluded"] == {"protected": 1, "already_locked": 1, "non_dialog": 1}
    assert summary["never_logged_on"] == 1  # NEWHIRE is recent, GHOST is not
    assert report["su10_user_list"].splitlines() == ["JSMITH", "GHOST", "CONTRACT"]


def test_user_analysis_api_csv_and_validation(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    with TestClient(app) as client:
        csv = client.post("/api/v1/users/analyze?format=csv", json={"usr02_csv": USR02, "dormant_days": 90}, headers=HEADERS)
        assert csv.status_code == 200 and csv.text.startswith("user,user_type")
        bad = client.post("/api/v1/users/analyze", json={"usr02_csv": "A,B\n1,2\n"}, headers=HEADERS)
        assert bad.status_code == 422 and "last logon" in bad.json()["detail"]


# ------------------------------------------------------------------ transports
E070 = """TRKORR,TRSTATUS,AS4DATE,AS4TIME,AS4USER,STRKORR
DEVK900100,R,20261101,100000,ALICE,
DEVK900101,R,20261102,100000,ALICE,DEVK900100
DEVK900110,R,20261105,090000,BOB,
DEVK900120,R,20261103,120000,CAROL,
DEVK900130,D,20261106,120000,DAVE,
DEVK900090,R,20261020,080000,EVE,
"""
E071 = """TRKORR,PGMID,OBJECT,OBJ_NAME,OBJFUNC
DEVK900101,R3TR,PROG,ZSALES_REPORT,
DEVK900100,R3TR,TABU,TVARVC,K
DEVK900110,R3TR,PROG,ZSALES_REPORT,
DEVK900110,R3TR,TABL,ZSALES_HDR,
DEVK900120,R3TR,CLAS,ZCL_PRICING,
DEVK900120,R3TR,PROG,ZSALES_REPORT,
DEVK900130,R3TR,PROG,ZOLD,D
DEVK900090,R3TR,CLAS,ZCL_PRICING,
"""


def test_transport_precheck_detects_risks():
    result = transports.precheck(
        E071, E070, queue=["DEVK900100", "DEVK900110", "DEVK900120", "DEVK900130"],
        imported_in_target=["DEVK900050"],
    )
    codes = {(f["code"], f["request"]) for f in result["findings"]}
    assert ("UNAPPROVED_PROTECTED_TABLE", "DEVK900100") in codes  # task rolled up to request
    assert ("SEQUENCE_INVERSION", "DEVK900120") in codes  # older ZSALES_REPORT imported after newer
    assert ("OBJECT_COLLISION", "DEVK900120") in codes
    assert ("NOT_RELEASED", "DEVK900130") in codes
    assert ("OBJECT_DELETION", "DEVK900130") in codes
    assert ("DDIC_TABLE_CHANGE", "DEVK900110") in codes
    assert ("MISSING_PREDECESSOR", "DEVK900120") in codes  # ZCL_PRICING in DEVK900090 not imported
    assert result["verdict"] == "block" and result["findings"][0]["severity"] == "error"


def test_transport_precheck_approval_and_default_order():
    result = transports.precheck(E071, E070, approved_tables=["TVARVC"])
    codes = {f["code"] for f in result["findings"]}
    assert "UNAPPROVED_PROTECTED_TABLE" not in codes and "SEQUENCE_INVERSION" not in codes
    order = [q["request"] for q in result["queue"]]
    assert order.index("DEVK900120") < order.index("DEVK900110")  # sorted by release time


def test_transport_postcheck_csv_and_log_text(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    sent = capture_alerts(monkeypatch)
    with TestClient(app) as client:
        csv_log = "TRKORR;RC\nDEVK900100;0004\nDEVK900110;0008\nDEVK900110;0000\nDEVK900120;0012\n"
        result = client.post("/api/v1/transports/postcheck", headers=HEADERS,
                             json={"target_system": "QAS", "log": csv_log}).json()
        assert {r["request"]: r["result"] for r in result["results"]} == {
            "DEVK900100": "warning", "DEVK900110": "error", "DEVK900120": "fatal"}
        assert result["failed"] == 2 and len(sent) == 2
        client.post("/api/v1/transports/postcheck", headers=HEADERS, json={"target_system": "QAS", "log": csv_log})
        assert len(sent) == 2  # de-duplicated
    text_log = "START IMPORT DEVK900200 ...\nimport of DEVK900200 finished, return code: 8\ntp rc=0 for DEVK900201\n"
    assert transports.parse_import_results(text_log) == {"DEVK900200": 8, "DEVK900201": 0}


def test_toolkit_endpoints_require_api_key(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    with TestClient(app) as client:
        assert client.post("/api/v1/users/analyze", json={"usr02_csv": USR02}).status_code == 401
        assert client.get("/api/v1/growth/HDB/report").status_code == 401


def test_dashboard_is_served(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.get("/")
    assert r.status_code == 200 and "SAP Basis Toolkit" in r.text
