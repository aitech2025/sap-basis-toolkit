from fastapi.testclient import TestClient

from app import config, db
from app.main import app

HEADERS = {"X-API-Key": "test-secret"}


def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DATABASE_URL", f"sqlite:///{tmp_path / 'topology.db'}")
    monkeypatch.setattr(config, "API_AUTH_TOKEN", "test-secret")
    monkeypatch.setattr(config, "SECRET_KEY", "topology-test-secret")
    db.reset_engine_for_tests()


def system_body(sid="PRD", landscape="Finance"):
    return {"sid": sid, "name": sid, "product": "S4HANA", "stack": "ABAP",
            "database": "HANA", "environment": "PRD", "landscape": landscape}


def test_topology_assets_and_explicit_dependencies(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with TestClient(app) as client:
        system = client.post("/api/v1/systems", headers=HEADERS, json=system_body()).json()
        host = client.post("/api/v1/topology/assets", headers=HEADERS, json={
            "landscape": "Finance", "system_id": system["id"], "kind": "host",
            "name": "PRD application host", "hostname": "sapapp.example.test",
            "attributes": {"availability_zone": "az-a", "os": "Linux"}, "source": "hana",
        })
        assert host.status_code == 201, host.text
        assert host.json()["source"] == "manual"  # clients cannot spoof collector provenance
        hana = client.post("/api/v1/topology/assets", headers=HEADERS, json={
            "landscape": "Finance", "system_id": system["id"], "kind": "hana",
            "name": "PRD HANA", "hostname": "saphana.example.test", "port": 30015,
        })
        assert hana.status_code == 201, hana.text
        link = client.post("/api/v1/topology/links", headers=HEADERS, json={
            "source_asset_id": host.json()["id"], "target_asset_id": hana.json()["id"],
            "relationship": "uses_database", "notes": "Confirmed by Basis team",
        })
        assert link.status_code == 201, link.text
        graph = client.get("/api/v1/topology?landscape=Finance", headers=HEADERS).json()
        assert len(graph["assets"]) == 2
        assert graph["links"][0]["relationship"] == "uses_database"
        scoped = client.get(f"/api/v1/systems/{system['id']}/topology", headers=HEADERS).json()
        assert len(scoped["assets"]) == 2
        assert client.delete(f"/api/v1/topology/assets/{host.json()['id']}", headers=HEADERS).status_code == 204
        graph = client.get("/api/v1/topology?landscape=Finance", headers=HEADERS).json()
        assert len(graph["assets"]) == 1 and len(graph["links"]) == 0


def test_topology_rejects_secret_attributes_and_cross_landscape_links(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with TestClient(app) as client:
        secret = client.post("/api/v1/topology/assets", headers=HEADERS, json={
            "landscape": "Finance", "kind": "rfc_destination", "name": "PRD_RFC",
            "attributes": {"password": "never-store-this"},
        })
        assert secret.status_code == 422
        a = client.post("/api/v1/topology/assets", headers=HEADERS, json={
            "landscape": "Finance", "kind": "host", "name": "Finance host",
        }).json()
        b = client.post("/api/v1/topology/assets", headers=HEADERS, json={
            "landscape": "Retail", "kind": "middleware", "name": "Retail middleware",
        }).json()
        cross = client.post("/api/v1/topology/links", headers=HEADERS, json={
            "source_asset_id": a["id"], "target_asset_id": b["id"], "relationship": "depends_on",
        })
        assert cross.status_code == 422 and "same landscape" in cross.json()["detail"]


def test_topology_writes_require_admin(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/v1/topology/assets", json={
            "landscape": "Finance", "kind": "host", "name": "untrusted",
        })
        assert response.status_code == 401
