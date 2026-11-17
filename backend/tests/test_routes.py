from fastapi.testclient import TestClient

from app import config, db
from app.main import app

H = {"X-API-Key": "test-secret"}


def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(config, "API_AUTH_TOKEN", "test-secret")
    monkeypatch.setattr(config, "ALLOWED_TARGET_HOSTS", ("qa.example.test",))
    db.reset_engine_for_tests()


def sys(client, sid, env, product="S4HANA", stack="ABAP", database="HANA", landscape="Finance"):
    r = client.post("/api/v1/systems", headers=H, json={
        "sid": sid, "name": sid, "product": product, "stack": stack, "database": database,
        "environment": env, "landscape": landscape})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def route(client, src, dst, kind="delivery", **extra):
    return client.post("/api/v1/routes", headers=H, json={
        "source_system_id": src, "target_system_id": dst, "route_type": kind, **extra})


def test_s4_landscape_dev_qas_prd_topology(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with TestClient(app) as c:
        d, q, p = sys(c, "S4D", "DEV"), sys(c, "S4Q", "QAS"), sys(c, "S4P", "PRD")
        bd, bp = sys(c, "BWD", "DEV", "BW4HANA"), sys(c, "BWP", "PRD", "BW4HANA")
        r1 = route(c, d, q, "consolidation", transport_layer="zs4d")
        assert r1.status_code == 201 and r1.json()["transport_layer"] == "ZS4D" and r1.json()["mechanism"] == "TMS"
        assert route(c, q, p).status_code == 201
        topo = c.get("/api/v1/landscapes/Finance/topology", headers=H).json()
        assert [t["key"] for t in topo["tiers"]] == ["DEV", "QAS", "PRD"]
        lanes = {lane["product"]: lane for lane in topo["lanes"]}
        assert [s["sid"] for s in lanes["S4HANA"]["systems"]] == ["S4D", "S4Q", "S4P"]
        assert lanes["S4HANA"]["path"] == "S4D → S4Q → S4P"
        assert len(topo["routes"]) == 2
        assert "SAP BW/4HANA systems have no transport routes yet" in topo["warnings"]
        # suggestions fill the BW track only
        sugg = c.post("/api/v1/landscapes/Finance/routes/suggest", headers=H).json()["proposals"]
        assert [(x["source_sid"], x["target_sid"], x["route_type"]) for x in sugg] == [("BWD", "BWP", "consolidation")]
        applied = c.post("/api/v1/landscapes/Finance/routes/suggest?apply=true", headers=H).json()
        assert len(applied["created"]) == 1
        path = c.get(f"/api/v1/systems/{q}/routes", headers=H).json()
        assert [[x["sid"] for x in hop] for hop in path["hops"]] == [["S4D"], ["S4Q"], ["S4P"]]
        assert len(path["inbound"]) == 1 and len(path["outbound"]) == 1
        assert bd and bp


def test_route_validation(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with TestClient(app) as c:
        d, q, p = sys(c, "S4D", "DEV"), sys(c, "S4Q", "QAS"), sys(c, "S4P", "PRD")
        q2 = sys(c, "S4X", "QAS")
        java = sys(c, "EPD", "DEV", "PORTAL", "JAVA", "ORACLE")
        assert route(c, d, d).status_code == 422
        backwards = route(c, p, d)
        assert backwards.status_code == 422 and "backwards" in backwards.json()["detail"]
        assert "cannot share" in route(c, java, q).json()["detail"]
        assert route(c, d, q, "consolidation").status_code == 201
        assert route(c, d, q, "consolidation").json()["detail"] == "This route already exists"
        # same tier is allowed (QAS -> QAS), but not a cycle
        assert route(c, q, q2).status_code == 201
        cycle = route(c, q2, q)
        assert cycle.status_code == 422 and "cycle" in cycle.json()["detail"]
        assert route(c, d, 999).status_code == 404
        assert route(c, d, p, "bogus").status_code == 422
        warn = route(c, q, p, "consolidation").json()
        assert "Consolidation routes normally start" in warn["warnings"][0]


def test_routes_follow_system_lifecycle(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with TestClient(app) as c:
        d, q = sys(c, "ECD", "DEV", "ECC", database="ORACLE", landscape="Retail"), sys(c, "ECQ", "QAS", "ECC", database="ORACLE", landscape="Retail")
        rid = route(c, d, q, "consolidation").json()["id"]
        assert len(c.get("/api/v1/routes?landscape=Retail", headers=H).json()) == 1
        # moving a system out of the landscape is reported, deleting it removes its routes
        c.patch(f"/api/v1/systems/{q}", headers=H, json={"landscape": "Other"})
        topo = c.get("/api/v1/landscapes/Retail/topology", headers=H).json()
        assert topo["routes"] == [] and "leaves landscape" in topo["warnings"][0]
        c.delete(f"/api/v1/systems/{q}", headers=H)
        assert c.get("/api/v1/routes", headers=H).json() == []
        assert c.delete(f"/api/v1/routes/{rid}", headers=H).status_code == 404
        assert c.get("/api/v1/landscapes/Nope/topology", headers=H).status_code == 404
        bad = c.patch(f"/api/v1/systems/{d}", headers=H, json={"landscape": "a/b"})
        assert bad.status_code == 422


def test_route_shows_last_import_on_target(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with TestClient(app) as c:
        d, q = sys(c, "S4D", "DEV"), sys(c, "S4Q", "QAS")
        route(c, d, q, "consolidation")
        c.post(f"/api/v1/systems/{q}/transports/postcheck", headers=H, json={"log": "TRKORR;RC\nS4DK900001;0008\n", "alert": False})
        topo = c.get("/api/v1/landscapes/Finance/topology", headers=H).json()
        assert topo["routes"][0]["last_import"]["status"] == "fail"
        assert topo["lanes"][0]["systems"][1]["health"] == "critical"


def test_branching_path_has_parallel_hops(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with TestClient(app) as c:
        d, x, q = sys(c, "S4D", "DEV"), sys(c, "S4X", "DEV"), sys(c, "S4Q", "QAS")
        t, r, p = sys(c, "S4T", "TRN"), sys(c, "S4R", "PRE"), sys(c, "S4P", "PRD")
        for a, b, k in ((d, q, "consolidation"), (x, q, "consolidation"), (q, r, "delivery"), (q, t, "delivery"), (r, p, "delivery")):
            assert route(c, a, b, k).status_code == 201
        hops = c.get(f"/api/v1/systems/{q}/routes", headers=H).json()["hops"]
        assert [[h["sid"] for h in layer] for layer in hops] == [["S4D", "S4X"], ["S4Q"], ["S4R", "S4T"], ["S4P"]]
        hops = c.get(f"/api/v1/systems/{p}/routes", headers=H).json()["hops"]
        assert [[h["sid"] for h in layer] for layer in hops] == [["S4D", "S4X"], ["S4Q"], ["S4R"], ["S4P"]]
        lane = c.get("/api/v1/landscapes/Finance/topology", headers=H).json()["lanes"][0]
        assert lane["path"] == "S4D → S4Q → S4R → S4P | S4X → S4Q → S4R → S4P"
