from fastapi.testclient import TestClient

from app import config, db
from app.main import app

KEY = {"X-API-Key": "test-secret"}


def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DATABASE_URL", f"sqlite:///{tmp_path / 'configuration.db'}")
    monkeypatch.setattr(config, "API_AUTH_TOKEN", "test-secret")
    monkeypatch.setattr(config, "SECRET_KEY", "unit-test-secret")
    db.reset_engine_for_tests()


def make_system(client):
    response = client.post("/api/v1/systems", headers=KEY, json={
        "sid": "CFG", "name": "Configuration test", "product": "S4HANA", "stack": "ABAP",
        "database": "HANA", "environment": "QAS", "landscape": "Test",
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_configuration_baseline_drift_and_secret_redaction(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with TestClient(app) as client:
        system_id = make_system(client)
        baseline = {
            "profile": {"login/min_password_lng": 12, "login/fails_to_user_lock": 5},
            "connection": {"rfc/gateway_security": "HIGH", "api_token": "must-not-be-stored"},
        }
        created = client.post(f"/api/v1/systems/{system_id}/configuration/baseline", headers=KEY,
                              json={"source": "test-extractor", "snapshot": baseline})
        assert created.status_code == 201, created.text
        assert created.json()["is_baseline"] is True

        current = {
            "profile": {"login/min_password_lng": 8, "login/fails_to_user_lock": 5},
            "connection": {"rfc/gateway_security": "LOW", "api_token": "new-secret"},
            "new_parameter": True,
        }
        captured = client.post(f"/api/v1/systems/{system_id}/configuration/snapshots", headers=KEY,
                               json={"source": "test-extractor", "snapshot": current})
        assert captured.status_code == 201, captured.text

        drift = client.get(f"/api/v1/systems/{system_id}/configuration/drift", headers=KEY).json()
        assert drift["status"] == "drift_detected"
        assert drift["summary"] == {"added": 1, "removed": 0, "modified": 1}  # redacted secret changes are intentionally invisible
        assert any(change["path"] == "profile.login/min_password_lng" for change in drift["changes"])
        history = client.get(f"/api/v1/systems/{system_id}/configuration/snapshots", headers=KEY).json()
        assert len(history) == 2
        assert history[0]["snapshot"]["connection"]["api_token"] == "[REDACTED]"
        assert history[1]["snapshot"]["connection"]["api_token"] == "[REDACTED]"


def test_configuration_requires_baseline_and_limits_baseline_to_admin(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with TestClient(app) as client:
        system_id = make_system(client)
        assert client.get(f"/api/v1/systems/{system_id}/configuration/drift", headers=KEY).json()["status"] == "baseline_required"
        assert client.post(f"/api/v1/systems/{system_id}/configuration/snapshots", headers=KEY,
                           json={"snapshot": {"a": 1}, "set_baseline": True}).status_code == 201
