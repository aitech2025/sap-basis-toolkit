from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import config, db
from app.main import app
from app.services import analytics

KEY = {"X-API-Key": "analytics-test-key"}


def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DATABASE_URL", f"sqlite:///{tmp_path / 'analytics.db'}")
    monkeypatch.setattr(config, "API_AUTH_TOKEN", "analytics-test-key")
    monkeypatch.setattr(config, "ALLOWED_TARGET_HOSTS", ("qa.example.test",))
    monkeypatch.setattr(config, "SECRET_KEY", "analytics-test-secret")
    db.reset_engine_for_tests()


def create_system(client, sid, landscape="Finance"):
    response = client.post("/api/v1/systems", headers=KEY, json={
        "sid": sid, "name": f"{sid} system", "product": "S4HANA", "stack": "ABAP",
        "database": "HANA", "environment": "PRD", "landscape": landscape,
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_analytics_series_aggregates_by_bucket_and_system(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    with TestClient(app) as client:
        a = create_system(client, "A4P")
        b = create_system(client, "B4P")
        with db.get_db() as connection:
            connection.execute(db.metric_points.insert(), [
                {"system_id": a["id"], "metric": "hana.cpu_pct", "value": 20.0, "ts": now - timedelta(minutes=4)},
                {"system_id": a["id"], "metric": "hana.cpu_pct", "value": 40.0, "ts": now - timedelta(minutes=3)},
                {"system_id": b["id"], "metric": "hana.cpu_pct", "value": 70.0, "ts": now - timedelta(minutes=3)},
            ])
        response = client.get("/api/v1/analytics/series?metric=hana.cpu_pct&hours=1&bucket_seconds=3600&aggregation=avg", headers=KEY)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["series"]) == 2
        by_sid = {item["sid"]: item for item in payload["series"]}
        assert by_sid["A4P"]["points"][0]["value"] == 30.0
        assert by_sid["A4P"]["points"][0]["samples"] == 2
        assert by_sid["B4P"]["points"][0]["value"] == 70.0


def test_analytics_filters_landscape_and_rejects_unknown_metric(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)
    with TestClient(app) as client:
        system = create_system(client, "C4P", "Retail")
        with db.get_db() as connection:
            connection.execute(db.metric_points.insert().values(
                system_id=system["id"], metric="hana.memory_used_pct", value=65.0, ts=now,
            ))
        filtered = client.get("/api/v1/analytics/series?metric=hana.memory_used_pct&landscape=Retail", headers=KEY)
        assert filtered.status_code == 200 and len(filtered.json()["series"]) == 1
        missing = client.get("/api/v1/analytics/series?metric=unknown.metric", headers=KEY)
        assert missing.status_code == 422
        summary = client.get("/api/v1/analytics/summary?hours=24&landscape=Retail", headers=KEY)
        assert summary.status_code == 200
        assert summary.json()["systems_reporting"] == 1
        assert summary.json()["samples"] == 1


def test_analytics_requires_authentication(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/v1/analytics/metrics").status_code == 401
