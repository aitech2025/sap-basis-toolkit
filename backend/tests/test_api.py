from fastapi.testclient import TestClient

from app.main import app
from app import db, config
from app.db import targets


def setup_test_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(config, "API_AUTH_TOKEN", "test-secret")
    db.reset_engine_for_tests()


def test_health_is_public(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200 and response.json()["status"] == "ok"


def test_api_requires_key(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/v1/targets").status_code == 401
        assert client.get("/api/v1/targets", headers={"X-API-Key": "wrong"}).status_code == 401
        assert client.get("/api/v1/targets", headers={"X-API-Key": "test-secret"}).status_code == 200


def test_api_fails_closed_without_configured_token(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "API_AUTH_TOKEN", "")
    with TestClient(app) as client:
        assert client.get("/api/v1/targets", headers={"X-API-Key": "anything"}).status_code == 503


def test_target_registration_and_duplicate(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    headers = {"X-API-Key": "test-secret"}
    with TestClient(app) as client:
        payload = {"name": "SAP QA", "hostname": "QA.Example.Test.", "port": 443}
        created = client.post("/api/v1/targets", json=payload, headers=headers)
        assert created.status_code == 201 and created.json()["hostname"] == "qa.example.test"
        assert client.post("/api/v1/targets", json=payload, headers=headers).status_code == 409
        assert len(client.get("/api/v1/targets", headers=headers).json()) == 1


def test_target_can_be_disabled(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    headers = {"X-API-Key": "test-secret"}
    with TestClient(app) as client:
        target = client.post(
            "/api/v1/targets", json={"name": "SAP QA", "hostname": "qa.example.test"},
            headers=headers,
        ).json()
        disabled = client.patch(f"/api/v1/targets/{target['id']}/disable", headers=headers)
        assert disabled.status_code == 200 and disabled.json()["enabled"] is False
        assert client.post(f"/api/v1/targets/{target['id']}/check", headers=headers).status_code == 404


def test_check_persists_and_reports(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    from app.services import checks
    monkeypatch.setattr(checks, "inspect_certificate", lambda host, port: {
        "subject": "CN=qa.example.test", "issuer": "CN=Test CA", "serial_number": "AB12",
        "not_before": "2026-01-01T00:00:00+00:00", "not_after": "2026-12-01T00:00:00+00:00",
        "days_remaining": 14, "tls_verified": True, "status": "warning", "error": None,
    })
    headers = {"X-API-Key": "test-secret"}
    with TestClient(app) as client:
        target = client.post(
            "/api/v1/targets", json={"name": "SAP QA", "hostname": "qa.example.test"},
            headers=headers,
        ).json()
        response = client.post(f"/api/v1/targets/{target['id']}/check", headers=headers)
        assert response.status_code == 200 and response.json()["days_remaining"] == 14
        assert len(client.get("/api/v1/checks", headers=headers).json()) == 1


def test_unreachable_alert_is_sent_once_per_outage(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    from app.services import checks
    sent = []
    monkeypatch.setattr(checks, "ALERT_WEBHOOK_URL", "https://hooks.example.test")
    monkeypatch.setattr(checks, "send_webhook", lambda payload: (sent.append(payload) or True, None))
    headers = {"X-API-Key": "test-secret"}
    with TestClient(app) as client:
        target = client.post(
            "/api/v1/targets", json={"name": "SAP QA", "hostname": "qa.example.test"},
            headers=headers,
        ).json()
        monkeypatch.setattr(checks, "inspect_certificate", lambda host, port: {
            "subject": None, "issuer": None, "serial_number": None, "not_before": None,
            "not_after": None, "days_remaining": None, "tls_verified": False,
            "status": "unreachable", "error": "connection timed out",
        })
        client.post(f"/api/v1/targets/{target['id']}/check", headers=headers)
        client.post(f"/api/v1/targets/{target['id']}/check", headers=headers)
        assert len(sent) == 1
        monkeypatch.setattr(checks, "inspect_certificate", lambda host, port: {
            "subject": "CN=qa.example.test", "issuer": "CN=Test CA", "serial_number": "AB12",
            "not_before": "2026-01-01T00:00:00+00:00", "not_after": "2026-12-01T00:00:00+00:00",
            "days_remaining": 200, "tls_verified": True, "status": "valid", "error": None,
        })
        client.post(f"/api/v1/targets/{target['id']}/check", headers=headers)
        monkeypatch.setattr(checks, "inspect_certificate", lambda host, port: {
            "subject": None, "issuer": None, "serial_number": None, "not_before": None,
            "not_after": None, "days_remaining": None, "tls_verified": False,
            "status": "unreachable", "error": "connection timed out",
        })
        client.post(f"/api/v1/targets/{target['id']}/check", headers=headers)
        assert len(sent) == 2


def test_scheduler_sweeps_enabled_targets(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    from app.services import scheduler
    with db.get_db() as connection:
        connection.execute(targets.insert().values(
            name="SAP QA", hostname="qa.example.test", port=443, enabled=True
        ))
    seen = []
    monkeypatch.setattr(scheduler, "check_target", lambda target_id: seen.append(target_id))
    scheduler.run_scheduled_checks()
    assert len(seen) == 1
