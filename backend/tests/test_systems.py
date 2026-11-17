import json

from fastapi.testclient import TestClient

from app import config, db
from app.main import app
from app.services import profiles

HEADERS = {"X-API-Key": "test-secret"}


def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(config, "API_AUTH_TOKEN", "test-secret")
    monkeypatch.setattr(config, "ALLOWED_TARGET_HOSTS", ("qa.example.test",))
    monkeypatch.setattr(config, "ALLOWED_TARGET_PORTS", (443,))
    db.reset_engine_for_tests()


def base(**over):
    return {"sid": "PRD", "name": "S/4 production", "product": "S4HANA", "stack": "ABAP",
            "database": "HANA", "environment": "PRD", **over}


def test_catalogue_lists_products_and_models(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with TestClient(app) as client:
        cat = client.get("/api/v1/meta/catalogue", headers=HEADERS).json()
    keys = {p["key"] for p in cat["products"]}
    assert {"ECC", "S4HANA", "NW_ABAP", "NW_JAVA", "BW", "PI_PO"} <= keys
    assert {m["key"] for m in cat["license_models"]} == {"NAMED_USER", "FUE"}


def test_system_crud_and_validation(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with TestClient(app) as client:
        created = client.post("/api/v1/systems", headers=HEADERS, json=base(sid="prd", landscape=" Finance "))
        assert created.status_code == 201
        system = created.json()
        assert system["sid"] == "PRD" and system["landscape"] == "Finance"
        assert system["effective_license_model"] == "FUE"
        # same SID in another landscape is fine, same landscape conflicts
        assert client.post("/api/v1/systems", headers=HEADERS, json=base(landscape="Finance")).status_code == 409
        assert client.post("/api/v1/systems", headers=HEADERS, json=base(landscape="Retail")).status_code == 201
        # product rules
        bad = client.post("/api/v1/systems", headers=HEADERS, json=base(sid="ABC", database="ORACLE"))
        assert bad.status_code == 422 and "requires database HANA" in bad.json()["detail"]
        bad = client.post("/api/v1/systems", headers=HEADERS, json=base(sid="ABC", product="ECC", stack="JAVA", database="ORACLE"))
        assert bad.status_code == 422 and "does not run on stack JAVA" in bad.json()["detail"]
        bad = client.post("/api/v1/systems", headers=HEADERS, json=base(sid="1AB"))
        assert bad.status_code == 422
        # detail includes capabilities with DB-specific export guidance
        detail = client.get(f"/api/v1/systems/{system['id']}", headers=HEADERS).json()
        assert "M_CS_TABLES" in detail["capabilities"]["growth"]["how_to_export"]
        assert detail["capabilities"]["transports"]["supported"] is True
        # update: override license model, filter, delete
        patched = client.patch(f"/api/v1/systems/{system['id']}", headers=HEADERS, json={"license_model": "NAMED_USER"})
        assert patched.json()["effective_license_model"] == "NAMED_USER"
        assert client.patch(f"/api/v1/systems/{system['id']}", headers=HEADERS, json={"database": "MSSQL"}).status_code == 422
        assert len(client.get("/api/v1/systems?landscape=Retail", headers=HEADERS).json()) == 1
        assert {l["landscape"] for l in client.get("/api/v1/landscapes", headers=HEADERS).json()} == {"Finance", "Retail"}
        assert client.delete(f"/api/v1/systems/{system['id']}", headers=HEADERS).status_code == 204
        assert client.get(f"/api/v1/systems/{system['id']}", headers=HEADERS).status_code == 404


def test_ecc_oracle_profile_and_bw_housekeeping():
    ecc = {"product": "ECC", "stack": "ABAP", "database": "ORACLE", "license_model": None}
    caps = profiles.capabilities(ecc)
    assert "DBA_SEGMENTS" in caps["growth"]["how_to_export"]
    assert caps["license_model"]["key"] == "NAMED_USER"
    assert "RSBERRORLOG" in profiles.housekeeping_catalogue("BW4HANA")
    assert "RSBERRORLOG" not in profiles.housekeeping_catalogue("ECC")
    assert "BC_MSG" in profiles.housekeeping_catalogue("PI_PO")


def test_profile_overrides_add_products(monkeypatch, tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps({"products": {"MDG": {"label": "SAP MDG", "stacks": ["ABAP"], "license_model": "FUE"}},
                                "housekeeping": {"zlog": "Run ZLOG_CLEANUP"}}))
    monkeypatch.setattr(config, "PROFILE_OVERRIDES_FILE", str(path))
    profiles._overrides.cache_clear()
    try:
        assert profiles.product("MDG")["label"] == "SAP MDG"
        assert profiles.housekeeping_catalogue("ECC")["ZLOG"] == "Run ZLOG_CLEANUP"
        assert profiles.validate_system("MDG", "ABAP", "HANA", None) == []
    finally:
        profiles._overrides.cache_clear()


def test_targets_are_linked_to_systems_and_paused_with_system(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    from app.services import scheduler
    seen = []
    monkeypatch.setattr(scheduler, "check_target", lambda target_id: seen.append(target_id))
    with TestClient(app) as client:
        system = client.post("/api/v1/systems", headers=HEADERS, json=base()).json()
        target = client.post("/api/v1/targets", headers=HEADERS,
                             json={"name": "PRD ICM", "hostname": "qa.example.test", "system_id": system["id"]}).json()
        assert target["system_id"] == system["id"]
        assert client.post("/api/v1/targets", headers=HEADERS,
                           json={"name": "x", "hostname": "qa.example.test", "port": 443, "system_id": 999}).status_code in (404, 409)
        summary = client.get(f"/api/v1/systems/{system['id']}/certificates", headers=HEADERS).json()
        assert summary["endpoints"][0]["sid"] == "PRD" and summary["endpoints"][0]["status"] == "pending"
        scheduler.run_scheduled_checks()
        assert seen == [target["id"]]
        client.patch(f"/api/v1/systems/{system['id']}", headers=HEADERS, json={"enabled": False})
        seen.clear()
        scheduler.run_scheduled_checks()
        assert seen == []
        unlinked = client.patch(f"/api/v1/targets/{target['id']}", headers=HEADERS, json={"system_id": None}).json()
        assert unlinked["system_id"] is None


def test_existing_targets_table_gets_system_column(tmp_path, monkeypatch):
    """Databases created by the certificate MVP are upgraded in place."""
    from sqlalchemy import create_engine, inspect, text
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    engine = create_engine(url)
    with engine.begin() as c:
        c.execute(text("CREATE TABLE targets (id INTEGER PRIMARY KEY, name VARCHAR(120) NOT NULL, hostname VARCHAR(253) NOT NULL, "
                       "port INTEGER NOT NULL, enabled BOOLEAN NOT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, "
                       "source VARCHAR(30) DEFAULT 'endpoint' NOT NULL, sap_system_id VARCHAR(3), sap_client VARCHAR(3), pse_name VARCHAR(255))"))
        c.execute(text("INSERT INTO targets (name, hostname, port, enabled) VALUES ('old', 'qa.example.test', 443, 1)"))
    engine.dispose()
    monkeypatch.setattr(db, "DATABASE_URL", url)
    db.reset_engine_for_tests()
    db.init_db()
    columns = {c["name"] for c in inspect(db.get_engine()).get_columns("targets")}
    assert "system_id" in columns
    db.init_db()  # idempotent
