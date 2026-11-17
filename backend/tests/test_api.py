from fastapi.testclient import TestClient

from app.main import app
from app import db


def test_health():
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_target_registration_and_duplicate(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "test.db")
    with TestClient(app) as client:
        payload = {"name": "SAP QA", "hostname": "qa.example.test", "port": 443}
        created = client.post("/api/v1/targets", json=payload)
        assert created.status_code == 201
        assert created.json()["hostname"] == "qa.example.test"

        duplicate = client.post("/api/v1/targets", json=payload)
        assert duplicate.status_code == 409

        listed = client.get("/api/v1/targets")
        assert listed.status_code == 200
        assert len(listed.json()) == 1
