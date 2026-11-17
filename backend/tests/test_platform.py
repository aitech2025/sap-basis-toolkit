from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import auth, config, db
from app.main import app
from app.services import incidents, notifications

KEY = {"X-API-Key": "test-secret"}
PW = "correct-horse-battery"


def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(config, "API_AUTH_TOKEN", "test-secret")
    monkeypatch.setattr(config, "ALLOWED_TARGET_HOSTS", ("qa.example.test",))
    monkeypatch.setattr(config, "SECRET_KEY", "unit-test-secret")
    db.reset_engine_for_tests()
    sent = []
    monkeypatch.setattr(config, "ALERT_WEBHOOK_URL", "https://hooks.example.test")
    monkeypatch.setattr(notifications, "send_webhook", lambda payload: (sent.append(payload) or True, None))
    return sent


def login(client, username, password=PW):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def make_system(client, sid="S4P"):
    r = client.post("/api/v1/systems", headers=KEY, json={"sid": sid, "name": sid, "product": "S4HANA", "stack": "ABAP",
                                                           "database": "HANA", "environment": "PRD", "landscape": "Finance"})
    return r.json()


def test_first_run_setup_login_and_roles(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    with TestClient(app) as c:
        assert c.get("/api/v1/auth/status").json() == {"setup_required": True}
        assert c.post("/api/v1/auth/setup", json={"api_key": "wrong", "username": "admin", "password": PW}).status_code == 401
        weak = c.post("/api/v1/auth/setup", json={"api_key": "test-secret", "username": "admin", "password": "short"})
        assert weak.status_code == 422
        assert c.post("/api/v1/auth/setup", json={"api_key": "test-secret", "username": "Admin", "password": PW}).status_code == 201
        assert c.post("/api/v1/auth/setup", json={"api_key": "test-secret", "username": "x", "password": PW}).status_code == 409
        admin = login(c, "admin")
        assert c.get("/api/v1/me", headers=admin).json()["role"] == "admin"
        for name, role in (("olga", "operator"), ("vic", "viewer")):
            assert c.post("/api/v1/users", headers=admin, json={"username": name, "password": PW, "role": role}).status_code == 201
        op, viewer = login(c, "olga"), login(c, "vic")
        system = make_system(c)
        # viewer: read only
        assert c.get("/api/v1/systems", headers=viewer).status_code == 200
        assert c.post("/api/v1/systems", headers=viewer, json={}).status_code == 403
        assert c.get("/api/v1/users", headers=viewer).status_code == 403
        assert c.get("/api/v1/audit", headers=op).status_code == 403
        # operator: can act, cannot manage users/rules/collectors
        r = c.post("/api/v1/maintenance", headers=op, json={
            "scope_type": "system", "scope_value": str(system["id"]), "reason": "kernel patch",
            "starts_at": datetime.now(timezone.utc).isoformat(), "ends_at": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()})
        assert r.status_code == 201 and r.json()["created_by"] == "olga"
        assert c.post("/api/v1/rules", headers=op, json={"name": "x", "metric": "dumps.last_hour", "operator": ">=", "warning": 1}).status_code == 403
        assert c.post(f"/api/v1/systems/{system['id']}/collectors", headers=op, json={"type": "sapcontrol", "host": "qa.example.test", "port": 50013}).status_code == 403
        assert c.post("/api/v1/users", headers=op, json={"username": "z", "password": PW, "role": "admin"}).status_code == 403
        # audit trail records who did what
        actions = c.get("/api/v1/audit", headers=admin).json()
        assert any(a["username"] == "olga" and a["action"] == "POST /api/v1/maintenance" and a["status"] == 201 for a in actions)
        assert any(a["action"] == "login" and a["username"] == "vic" for a in actions)
        assert any(a["username"] == "vic" and a["status"] == 403 for a in actions)


def test_sessions_end_on_password_change_disable_and_tampering(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "ADMIN_PASSWORD", PW)
    with TestClient(app) as c:  # bootstrap admin from env
        admin = login(c, "admin")
        uid = c.post("/api/v1/users", headers=admin, json={"username": "olga", "password": PW, "role": "operator"}).json()["id"]
        op = login(c, "olga")
        assert c.get("/api/v1/me", headers=op).status_code == 200
        bad = {"Authorization": op["Authorization"][:-3] + "abc"}
        assert c.get("/api/v1/me", headers=bad).status_code == 401
        c.patch(f"/api/v1/users/{uid}", headers=admin, json={"active": False})
        assert c.get("/api/v1/me", headers=op).status_code == 401
        assert c.post("/api/v1/auth/login", json={"username": "olga", "password": PW}).status_code == 401
        # own password change returns a fresh token, old one is invalid
        r = c.post("/api/v1/me/password", headers=admin, json={"current_password": PW, "new_password": "another-long-password"}).json()
        assert c.get("/api/v1/me", headers=admin).status_code == 401
        assert c.get("/api/v1/me", headers={"Authorization": f"Bearer {r['token']}"}).status_code == 200
        # last admin cannot be demoted
        admin_id = next(u["id"] for u in auth.list_users() if u["username"] == "admin")
        new = {"Authorization": f"Bearer {r['token']}"}
        assert c.patch(f"/api/v1/users/{admin_id}", headers=new, json={"role": "viewer"}).status_code == 422


def test_login_throttling(monkeypatch, tmp_path):
    setup(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "ADMIN_PASSWORD", PW)
    with TestClient(app) as c:
        for _ in range(5):
            assert c.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong-password-x"}).status_code == 401
        assert c.post("/api/v1/auth/login", json={"username": "admin", "password": PW}).status_code == 429


def test_rules_incident_lifecycle_and_maintenance(monkeypatch, tmp_path):
    sent = setup(monkeypatch, tmp_path)
    with TestClient(app) as c:
        system = make_system(c)
        s = {**system}
        # breach -> open + notify
        r = incidents.evaluate(s, {"dumps.last_hour": 25})
        assert r == {"breaches": 1, "resolved": 0} and len(sent) == 1
        inc = c.get(f"/api/v1/incidents?system_id={system['id']}", headers=KEY).json()[0]
        assert inc["severity"] == "warning" and inc["status"] == "open" and "Short dumps" in inc["title"]
        # repeat -> same incident, no new notification; escalation -> notify
        incidents.evaluate(s, {"dumps.last_hour": 30})
        incidents.evaluate(s, {"dumps.last_hour": 150})
        detail = c.get(f"/api/v1/incidents/{inc['id']}", headers=KEY).json()
        assert detail["severity"] == "critical" and detail["occurrences"] == 3 and len(sent) == 2
        assert [e["kind"] for e in detail["timeline"]][:3] == ["opened", "notified", "escalated"]
        # people act on it
        assert c.post(f"/api/v1/incidents/{inc['id']}/acknowledge", headers=KEY, json={"message": "looking"}).json()["status"] == "acknowledged"
        assert c.post(f"/api/v1/incidents/{inc['id']}/acknowledge", headers=KEY, json={}).status_code == 409
        assert c.post(f"/api/v1/incidents/{inc['id']}/assign", headers=KEY, json={"assignee": "olga"}).json()["assignee"] == "olga"
        c.post(f"/api/v1/incidents/{inc['id']}/comment", headers=KEY, json={"message": "ST22: TIME_OUT in Z report"})
        # recovery -> auto-resolve + resolve notification
        assert incidents.evaluate(s, {"dumps.last_hour": 2}) == {"breaches": 0, "resolved": 1}
        detail = c.get(f"/api/v1/incidents/{inc['id']}", headers=KEY).json()
        assert detail["status"] == "resolved" and detail["resolved_by"] == "system" and "RESOLVED" in sent[-1]["summary"]
        assert c.get("/api/v1/incidents/counts", headers=KEY).json()["total"] == 0
        # system-specific override beats the global rule
        c.post("/api/v1/rules", headers=KEY, json={"name": "S4P dumps", "metric": "dumps.last_hour", "operator": ">=",
                                                    "warning": 500, "scope_type": "system", "scope_value": str(system["id"])})
        assert incidents.evaluate(s, {"dumps.last_hour": 150})["breaches"] == 0
        # maintenance: incident recorded, notification suppressed
        before = len(sent)
        c.post("/api/v1/maintenance", headers=KEY, json={"scope_type": "landscape", "scope_value": "Finance", "reason": "upgrade",
               "starts_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
               "ends_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()})
        incidents.evaluate(s, {"hana.memory_used_pct": 97})
        mem = c.get(f"/api/v1/incidents?system_id={system['id']}", headers=KEY).json()[0]
        assert mem["in_maintenance"] is True and len(sent) == before
        kinds = [e["kind"] for e in c.get(f"/api/v1/incidents/{mem['id']}", headers=KEY).json()["timeline"]]
        assert "suppressed" in kinds
        windows = c.get("/api/v1/maintenance", headers=KEY).json()
        assert windows[0]["state"] == "active"
        c.post(f"/api/v1/maintenance/{windows[0]['id']}/end", headers=KEY)
        assert c.get("/api/v1/maintenance", headers=KEY).json() == []
        # built-in rules cannot be deleted, custom ones can
        rules = c.get("/api/v1/rules", headers=KEY).json()
        builtin = next(r for r in rules if r["builtin"])
        assert c.delete(f"/api/v1/rules/{builtin['id']}", headers=KEY).status_code == 409
        custom = next(r for r in rules if not r["builtin"])
        assert c.delete(f"/api/v1/rules/{custom['id']}", headers=KEY).status_code == 204
        # overview health reflects open incidents
        overview = c.get("/api/v1/overview", headers=KEY).json()["systems"][0]
        assert overview["health"] == "critical" and overview["incidents"]["critical"] == 1


def test_module_alerts_become_incidents_and_respect_maintenance(monkeypatch, tmp_path):
    sent = setup(monkeypatch, tmp_path)
    with TestClient(app) as c:
        system = make_system(c)
        c.post(f"/api/v1/systems/{system['id']}/transports/postcheck", headers=KEY, json={"log": "TRKORR;RC\nS4DK900001;0008\n"})
        assert len(sent) == 1
        inc = c.get("/api/v1/incidents", headers=KEY).json()
        assert inc[0]["source"] == "module" and "S4DK900001" in inc[0]["title"]
        c.post("/api/v1/maintenance", headers=KEY, json={"scope_type": "global", "reason": "DR test",
               "starts_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
               "ends_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()})
        c.post(f"/api/v1/systems/{system['id']}/transports/postcheck", headers=KEY, json={"log": "TRKORR;RC\nS4DK900002;0012\n"})
        assert len(sent) == 1  # suppressed
        assert len(c.get("/api/v1/incidents", headers=KEY).json()) == 2
