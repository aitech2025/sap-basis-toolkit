from fastapi.testclient import TestClient
from app.main import app
from app import db
from app.db import targets

def setup_test_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    db.reset_engine_for_tests()

def test_health(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    with TestClient(app) as client: response = client.get("/health")
    assert response.status_code == 200 and response.json()["status"] == "ok"

def test_target_registration_and_duplicate(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    with TestClient(app) as client:
        payload = {"name": "SAP QA", "hostname": "QA.Example.Test.", "port": 443}
        created = client.post("/api/v1/targets", json=payload)
        assert created.status_code == 201 and created.json()["hostname"] == "qa.example.test"
        assert client.post("/api/v1/targets", json=payload).status_code == 409
        assert len(client.get("/api/v1/targets").json()) == 1

def test_check_persists_and_reports(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    from app.services import checks
    monkeypatch.setattr(checks, "inspect_certificate", lambda host, port: {"subject": "CN=qa.example.test", "issuer": "CN=Test CA", "serial_number": "AB12", "not_before": "2026-01-01T00:00:00+00:00", "not_after": "2026-12-01T00:00:00+00:00", "days_remaining": 14, "tls_verified": True, "status": "warning", "error": None})
    with TestClient(app) as client:
        target = client.post("/api/v1/targets", json={"name": "SAP QA", "hostname": "qa.example.test"}).json()
        response = client.post(f"/api/v1/targets/{target['id']}/check")
        assert response.status_code == 200 and response.json()["days_remaining"] == 14
        assert len(client.get("/api/v1/checks").json()) == 1

def test_scheduler_sweeps_enabled_targets(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    from app.services import scheduler
    with db.get_db() as connection: connection.execute(targets.insert().values(name="SAP QA", hostname="qa.example.test", port=443, enabled=True))
    seen = []; monkeypatch.setattr(scheduler, "check_target", lambda target_id: seen.append(target_id))
    scheduler.run_scheduled_checks()
    assert len(seen) == 1
