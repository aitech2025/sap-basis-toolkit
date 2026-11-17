from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import config, db
from app.main import app

KEY = {"X-API-Key": "intelligence-test-key"}


def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DATABASE_URL", f"sqlite:///{tmp_path / 'intelligence.db'}")
    monkeypatch.setattr(config, "API_AUTH_TOKEN", "intelligence-test-key")
    monkeypatch.setattr(config, "ALLOWED_TARGET_HOSTS", ("qa.example.test",))
    monkeypatch.setattr(config, "SECRET_KEY", "intelligence-test-secret")
    db.reset_engine_for_tests()


def create_system(client, sid="I4P", landscape="Finance"):
    response = client.post("/api/v1/systems", headers=KEY, json={
        "sid": sid, "name": f"{sid} system", "product": "S4HANA", "stack": "ABAP",
        "database": "HANA", "environment": "PRD", "landscape": landscape,
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_observed_availability_is_sample_ratio_and_does_not_infer_missing_time(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)
    with TestClient(app) as client:
        system = create_system(client)
        with db.get_db() as connection:
            connection.execute(db.metric_points.insert(), [
                {"system_id": system["id"], "metric": "availability.system_up", "value": 1, "ts": now - timedelta(minutes=5)},
                {"system_id": system["id"], "metric": "availability.system_up", "value": 0, "ts": now - timedelta(minutes=2)},
            ])
        response = client.get("/api/v1/intelligence/availability?hours=1", headers=KEY)
        assert response.status_code == 200, response.text
        item = response.json()["systems"][0]
        assert item["samples"] == 2
        assert item["up_samples"] == 1 and item["down_samples"] == 1
        assert item["observed_availability_pct"] == 50.0
        assert "not wall-clock SLA" in item["measurement"]


def test_anomaly_detection_requires_baseline_and_flags_robust_outlier(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)
    with TestClient(app) as client:
        system = create_system(client)
        rows = [
            {"system_id": system["id"], "metric": "hana.cpu_pct", "value": 20 + (i % 3), "ts": now - timedelta(hours=2, minutes=10-i)}
            for i in range(20)
        ]
        rows.append({"system_id": system["id"], "metric": "hana.cpu_pct", "value": 95, "ts": now - timedelta(minutes=5)})
        with db.get_db() as connection:
            connection.execute(db.metric_points.insert(), rows)
        response = client.get("/api/v1/intelligence/anomalies?hours=1", headers=KEY)
        assert response.status_code == 200, response.text
        anomalies = response.json()["anomalies"]
        assert len(anomalies) == 1
        assert anomalies[0]["metric"] == "hana.cpu_pct"
        assert anomalies[0]["direction"] == "high"


def test_capacity_forecast_returns_insufficient_data_without_fabricating_projection(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)
    with TestClient(app) as client:
        system = create_system(client)
        with db.get_db() as connection:
            connection.execute(db.capacity_snapshots.insert(), [
                {"system_id": system["id"], "disk_total_mb": 1000, "disk_used_mb": 700,
                 "memory_total_mb": 2000, "memory_used_mb": 1000, "captured_at": now - timedelta(days=1)},
                {"system_id": system["id"], "disk_total_mb": 1000, "disk_used_mb": 710,
                 "memory_total_mb": 2000, "memory_used_mb": 1010, "captured_at": now},
            ])
        response = client.get("/api/v1/intelligence/capacity-forecasts", headers=KEY)
        assert response.status_code == 200, response.text
        forecast = response.json()["systems"][0]["forecasts"]["disk"]
        assert forecast["status"] == "insufficient_data"
        assert forecast["threshold_in_days"] is None


def test_incident_correlations_are_temporal_not_causal(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)
    with TestClient(app) as client:
        system = create_system(client)
        with db.get_db() as connection:
            incident_id = connection.execute(db.incidents.insert().values(
                system_id=system["id"], fingerprint="test:incident", source="rule", metric="hana.cpu_pct",
                title="CPU pressure", severity="warning", status="open", details="{}",
                value=90, opened_at=now, last_seen_at=now, occurrences=1, in_maintenance=False,
            ).returning(db.incidents.c.id)).scalar_one()
            connection.execute(db.metric_points.insert(), [
                {"system_id": system["id"], "metric": "hana.cpu_pct", "value": 40, "ts": now - timedelta(minutes=10)},
                {"system_id": system["id"], "metric": "hana.cpu_pct", "value": 90, "ts": now + timedelta(minutes=1)},
            ])
        response = client.get("/api/v1/intelligence/incident-correlations?hours=1", headers=KEY)
        assert response.status_code == 200, response.text
        item = response.json()["incidents"][0]
        assert item["incident_id"] == incident_id
        assert item["related_metrics"][0]["change"] == 50
        assert "not proof of causation" in item["interpretation"]


def test_intelligence_endpoints_require_authentication(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/v1/intelligence/availability").status_code == 401
        assert client.get("/api/v1/intelligence/anomalies").status_code == 401


def test_capacity_forecast_projects_threshold_from_three_snapshots(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)
    with TestClient(app) as client:
        system = create_system(client)
        with db.get_db() as connection:
            connection.execute(db.capacity_snapshots.insert(), [
                {"system_id": system["id"], "disk_total_mb": 1000, "disk_used_mb": 800,
                 "memory_total_mb": 2000, "memory_used_mb": 1000, "captured_at": now - timedelta(days=20)},
                {"system_id": system["id"], "disk_total_mb": 1000, "disk_used_mb": 840,
                 "memory_total_mb": 2000, "memory_used_mb": 1020, "captured_at": now - timedelta(days=10)},
                {"system_id": system["id"], "disk_total_mb": 1000, "disk_used_mb": 880,
                 "memory_total_mb": 2000, "memory_used_mb": 1040, "captured_at": now},
            ])
        response = client.get("/api/v1/intelligence/capacity-forecasts?horizon_days=30&threshold_pct=85", headers=KEY)
        assert response.status_code == 200, response.text
        forecast = response.json()["systems"][0]["forecasts"]["disk"]
        assert forecast["status"] == "threshold_projected"
        assert forecast["daily_change_mb"] == 4.0
        assert forecast["threshold_in_days"] is not None and forecast["threshold_in_days"] < 30


def test_root_cause_ranks_correlated_hypotheses_with_evidence_and_limits(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)
    with TestClient(app) as client:
        system = create_system(client)
        with db.get_db() as connection:
            incident_id = connection.execute(db.incidents.insert().values(
                system_id=system["id"], fingerprint="rca:test", source="rule", metric="dumps.last_hour",
                title="Application slowdown", severity="critical", status="open", details="{}",
                value=25, opened_at=now, last_seen_at=now, occurrences=1, in_maintenance=False,
            ).returning(db.incidents.c.id)).scalar_one()
            samples = []
            for metric, before, after in [
                ("hana.cpu_pct", 35, 96),
                ("hana.memory_used_pct", 65, 92),
                ("wp.dia_busy_pct", 35, 91),
                ("wp.long_running", 0, 4),
                ("trfc.errors", 0, 0),
            ]:
                samples.extend([
                    {"system_id": system["id"], "metric": metric, "value": before, "ts": now - timedelta(minutes=10)},
                    {"system_id": system["id"], "metric": metric, "value": after, "ts": now + timedelta(minutes=2)},
                ])
            connection.execute(db.metric_points.insert(), samples)
            connection.execute(db.monitor_snapshots.insert().values(
                system_id=system["id"], monitor="hana", source="hana", captured_at=now,
                health="critical", data='{"summary":{"cpu_pct":96,"alerts":2}}',
            ))
        response = client.get(f"/api/v1/intelligence/root-cause/{incident_id}", headers=KEY)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["incident"]["sid"] == "I4P"
        domains = [h["domain"] for h in result["hypotheses"]]
        assert domains[0] == "HANA"
        assert "ABAP" in domains
        assert result["hypotheses"][0]["confidence"] in ("medium", "high")
        assert result["coverage"]["monitor_snapshots_in_window"] == 1
        assert any(e["metric"] == "hana.cpu_pct" and e["triggered"] for e in result["evidence"])
        assert "not causal proof" in result["interpretation"]


def test_root_cause_requires_auth_and_returns_404_for_missing_incident(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/v1/intelligence/root-cause/999").status_code == 401
        response = client.get("/api/v1/intelligence/root-cause/999", headers=KEY)
        assert response.status_code == 404


def test_root_cause_explains_incidents_without_system_association(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)
    with TestClient(app) as client:
        with db.get_db() as connection:
            incident_id = connection.execute(db.incidents.insert().values(
                system_id=None, fingerprint="rca:unassigned", source="module", metric=None,
                title="Unassigned platform alert", severity="warning", status="open", details="{}",
                value=None, opened_at=now, last_seen_at=now, occurrences=1, in_maintenance=False,
            ).returning(db.incidents.c.id)).scalar_one()
        response = client.get(f"/api/v1/intelligence/root-cause/{incident_id}", headers=KEY)
        assert response.status_code == 200
        assert response.json()["hypotheses"] == []
        assert response.json()["coverage"]["system_metrics"] is False
