from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.services.collectors import CollectorError, node_exporter, runner


def test_node_exporter_parser_extracts_only_allowlisted_root_signals():
    payload = """# HELP node_load1 1m load average
node_load1 2.5
node_memory_MemTotal_bytes 1000
node_memory_MemAvailable_bytes 250
node_filesystem_size_bytes{device="/dev/sda1",mountpoint="/",fstype="ext4"} 2000
node_filesystem_avail_bytes{device="/dev/sda1",mountpoint="/",fstype="ext4"} 500
node_network_receive_bytes_total{device="eth0"} 99999
"""
    metrics = node_exporter.parse_metrics(payload)
    assert metrics == {"host.load1": 2.5, "host.memory_used_pct": 75.0, "host.disk_used_pct": 75.0}
    assert not any("network" in key for key in metrics)


def test_node_exporter_rejects_unapproved_port():
    from app.services.collectors import validate_port
    with pytest.raises(CollectorError, match="port 9100"):
        validate_port("node_exporter", 9200)
    validate_port("node_exporter", 9100)


def test_node_exporter_collector_persists_metrics(monkeypatch, tmp_path):
    from app import db
    from app.services import monitors, notifications

    monkeypatch.setattr(db, "DATABASE_URL", f"sqlite:///{tmp_path / 'host-metrics.db'}")
    monkeypatch.setattr(config, "API_AUTH_TOKEN", "test-secret")
    monkeypatch.setattr(config, "SECRET_KEY", "unit-test-secret")
    monkeypatch.setattr(config, "SIMULATOR_ENABLED", True)
    monkeypatch.setattr(config, "ALLOWED_TARGET_HOSTS", ("*.corp.example.test",))
    monkeypatch.setattr(config, "ALLOWED_TARGET_CIDRS", ("10.20.0.0/16",))
    monkeypatch.setattr(notifications, "send_webhook", lambda payload: (True, None))
    db.reset_engine_for_tests()
    captured = datetime.now(timezone.utc)
    monkeypatch.setattr(node_exporter, "collect", lambda *args: {
        "metrics": {"host.load1": 1.2, "host.memory_used_pct": 82.0, "host.disk_used_pct": 70.0},
        "collected_at": captured,
    })
    with TestClient(app) as client:
        system = client.post("/api/v1/systems", headers={"X-API-Key": "test-secret"}, json={
            "sid": "HST", "name": "Host metrics", "product": "S4HANA", "stack": "ABAP",
            "database": "HANA", "environment": "PRD",
        }).json()
        collector = client.post(f"/api/v1/systems/{system['id']}/collectors",
            headers={"X-API-Key": "test-secret"}, json={
                "type": "node_exporter", "host": "sapapp.corp.example.test", "port": 9100,
                "use_tls": False, "interval_seconds": 300,
            })
        assert collector.status_code == 201, collector.text
        result = client.post(f"/api/v1/collectors/{collector.json()['id']}/run",
                             headers={"X-API-Key": "test-secret"})
        assert result.status_code == 200, result.text
        assert result.json()["metrics"]["host.memory_used_pct"] == 82.0
        assert "host" in client.get(f"/api/v1/systems/{system['id']}/monitors",
                                    headers={"X-API-Key": "test-secret"}).json()["monitors"]
