from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.main import app
from app import db, config
from app.db import targets


def setup_test_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(config, "API_AUTH_TOKEN", "test-secret")
    monkeypatch.setattr(config, "ALLOWED_TARGET_HOSTS", ("qa.example.test",))
    monkeypatch.setattr(config, "ALLOWED_TARGET_CIDRS", ())
    monkeypatch.setattr(config, "ALLOWED_TARGET_PORTS", (443,))
    db.reset_engine_for_tests()


def test_health_is_public(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200 and response.json()["status"] == "ok"


def test_api_requires_key(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/v1/targets").status_code == 401
        assert client.get("/api/v1/targets", headers={"X-API-Key": "wrong"}).status_code == 401
        assert client.get("/api/v1/targets", headers={"X-API-Key": "test-secret"}).status_code == 200


def test_api_fails_closed_without_configured_token(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "API_AUTH_TOKEN", "")
    with TestClient(app) as client:
        assert client.get("/api/v1/targets", headers={"X-API-Key": "anything"}).status_code == 503


def test_target_registration_and_duplicate(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    headers = {"X-API-Key": "test-secret"}
    with TestClient(app) as client:
        payload = {"name": "SAP QA", "hostname": "QA.Example.Test.", "port": 443}
        created = client.post("/api/v1/targets", json=payload, headers=headers)
        assert created.status_code == 201 and created.json()["hostname"] == "qa.example.test"
        assert client.post("/api/v1/targets", json=payload, headers=headers).status_code == 409
        assert len(client.get("/api/v1/targets", headers=headers).json()) == 1


def test_database_rejects_duplicate_endpoint(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    db.init_db()
    with db.get_db() as connection:
        connection.execute(targets.insert().values(name="SAP QA", hostname="qa.example.test", port=443))
    try:
        with db.get_db() as connection:
            connection.execute(targets.insert().values(name="SAP QA duplicate", hostname="qa.example.test", port=443))
    except IntegrityError:
        pass
    else:
        raise AssertionError("Database should enforce endpoint uniqueness")


def test_target_can_be_disabled(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    headers = {"X-API-Key": "test-secret"}
    with TestClient(app) as client:
        target = client.post(
            "/api/v1/targets", json={"name": "SAP QA", "hostname": "qa.example.test"},
            headers=headers,
        ).json()
        disabled = client.patch(f"/api/v1/targets/{target['id']}/disable", headers=headers)
        assert disabled.status_code == 200 and disabled.json()["enabled"] is False
        assert client.post(f"/api/v1/targets/{target['id']}/check", headers=headers).status_code == 404


def test_check_persists_and_reports(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    from app.services import checks
    monkeypatch.setattr(checks, "inspect_certificate", lambda host, port: {
        "subject": "CN=qa.example.test", "issuer": "CN=Test CA", "serial_number": "AB12",
        "not_before": "2026-01-01T00:00:00+00:00", "not_after": "2026-12-01T00:00:00+00:00",
        "days_remaining": 14, "tls_verified": True, "status": "warning", "error": None,
    })
    headers = {"X-API-Key": "test-secret"}
    with TestClient(app) as client:
        target = client.post(
            "/api/v1/targets", json={"name": "SAP QA", "hostname": "qa.example.test"},
            headers=headers,
        ).json()
        response = client.post(f"/api/v1/targets/{target['id']}/check", headers=headers)
        assert response.status_code == 200 and response.json()["days_remaining"] == 14
        assert len(client.get("/api/v1/checks", headers=headers).json()) == 1


def test_unreachable_alert_is_sent_once_per_outage(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    from app.services import checks
    sent = []
    monkeypatch.setattr(checks, "ALERT_WEBHOOK_URL", "https://hooks.example.test")
    monkeypatch.setattr(checks, "send_webhook", lambda payload: (sent.append(payload) or True, None))
    headers = {"X-API-Key": "test-secret"}
    with TestClient(app) as client:
        target = client.post(
            "/api/v1/targets", json={"name": "SAP QA", "hostname": "qa.example.test"},
            headers=headers,
        ).json()
        monkeypatch.setattr(checks, "inspect_certificate", lambda host, port: {
            "subject": None, "issuer": None, "serial_number": None, "not_before": None,
            "not_after": None, "days_remaining": None, "tls_verified": False,
            "status": "unreachable", "error": "connection timed out",
        })
        client.post(f"/api/v1/targets/{target['id']}/check", headers=headers)
        client.post(f"/api/v1/targets/{target['id']}/check", headers=headers)
        assert len(sent) == 1
        monkeypatch.setattr(checks, "inspect_certificate", lambda host, port: {
            "subject": "CN=qa.example.test", "issuer": "CN=Test CA", "serial_number": "AB12",
            "not_before": "2026-01-01T00:00:00+00:00", "not_after": "2026-12-01T00:00:00+00:00",
            "days_remaining": 200, "tls_verified": True, "status": "valid", "error": None,
        })
        client.post(f"/api/v1/targets/{target['id']}/check", headers=headers)
        monkeypatch.setattr(checks, "inspect_certificate", lambda host, port: {
            "subject": None, "issuer": None, "serial_number": None, "not_before": None,
            "not_after": None, "days_remaining": None, "tls_verified": False,
            "status": "unreachable", "error": "connection timed out",
        })
        client.post(f"/api/v1/targets/{target['id']}/check", headers=headers)
        assert len(sent) == 2


def test_scheduler_sweeps_enabled_targets(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    db.init_db()
    from app.services import scheduler
    with db.get_db() as connection:
        connection.execute(targets.insert().values(
            name="SAP QA", hostname="qa.example.test", port=443, enabled=True
        ))
    seen = []
    monkeypatch.setattr(scheduler, "check_target", lambda target_id: seen.append(target_id))
    scheduler.run_scheduled_checks()
    assert len(seen) == 1


def test_legacy_database_uniqueness_upgrade_is_safe(tmp_path):
    from sqlalchemy import create_engine, inspect, text
    from app.db import ensure_target_uniqueness

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE targets (id INTEGER PRIMARY KEY, hostname VARCHAR(253) NOT NULL, port INTEGER NOT NULL)"
        ))
        connection.execute(text(
            "INSERT INTO targets (hostname, port) VALUES "
            "('qa.example.test', 443), ('qa.example.test', 443)"
        ))
        try:
            ensure_target_uniqueness(connection)
        except RuntimeError as exc:
            assert "duplicate hostname/port" in str(exc)
        else:
            raise AssertionError("Migration must stop rather than discard duplicate targets")

        connection.execute(text("DELETE FROM targets WHERE id = 2"))
        ensure_target_uniqueness(connection)
        index_names = {item["name"] for item in inspect(connection).get_indexes("targets")}
        assert "uq_targets_hostname_port" in index_names
    engine.dispose()


def test_target_allowlist_rejects_unapproved_host(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    headers = {"X-API-Key": "test-secret"}
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/targets",
            json={"name": "Unapproved", "hostname": "metadata.google.internal"},
            headers=headers,
        )
    assert response.status_code == 403
    assert "not included" in response.json()["detail"]


def test_target_policy_requires_private_cidr(monkeypatch):
    import socket
    from app.services.target_policy import resolve_approved_addresses, TargetPolicyError

    monkeypatch.setattr(config, "ALLOWED_TARGET_HOSTS", ("sapqa.corp.example",))
    monkeypatch.setattr(config, "ALLOWED_TARGET_CIDRS", ())
    monkeypatch.setattr(config, "ALLOWED_TARGET_PORTS", (443,))
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.20.1.5", 443))],
    )
    try:
        resolve_approved_addresses("sapqa.corp.example", 443)
    except TargetPolicyError as exc:
        assert "ALLOWED_TARGET_CIDRS" in str(exc)
    else:
        raise AssertionError("Private SAP address must require an explicit CIDR allowlist")


def test_target_policy_accepts_approved_private_cidr(monkeypatch):
    import socket
    from app.services.target_policy import resolve_approved_addresses

    monkeypatch.setattr(config, "ALLOWED_TARGET_HOSTS", ("sapqa.corp.example",))
    monkeypatch.setattr(config, "ALLOWED_TARGET_CIDRS", ("10.20.0.0/16",))
    monkeypatch.setattr(config, "ALLOWED_TARGET_PORTS", (443,))
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.20.1.5", 443))],
    )
    assert resolve_approved_addresses("sapqa.corp.example", 443) == ["10.20.1.5"]


def test_target_policy_rejects_dns_answer_outside_cidr(monkeypatch):
    import socket
    from app.services.target_policy import resolve_approved_addresses, TargetPolicyError

    monkeypatch.setattr(config, "ALLOWED_TARGET_HOSTS", ("sapqa.corp.example",))
    monkeypatch.setattr(config, "ALLOWED_TARGET_CIDRS", ("10.20.0.0/16",))
    monkeypatch.setattr(config, "ALLOWED_TARGET_PORTS", (443,))
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))],
    )
    try:
        resolve_approved_addresses("sapqa.corp.example", 443)
    except TargetPolicyError as exc:
        assert "outside ALLOWED_TARGET_CIDRS" in str(exc)
    else:
        raise AssertionError("DNS answer outside the approved CIDRs must be rejected")


def test_target_policy_rejects_unapproved_port(monkeypatch):
    from app.services.target_policy import validate_target, TargetPolicyError
    monkeypatch.setattr(config, "ALLOWED_TARGET_HOSTS", ("sapqa.corp.example",))
    monkeypatch.setattr(config, "ALLOWED_TARGET_PORTS", (443,))
    try:
        validate_target("sapqa.corp.example", 22)
    except TargetPolicyError as exc:
        assert "ALLOWED_TARGET_PORTS" in str(exc)
    else:
        raise AssertionError("Unapproved destination port must be rejected")


def test_target_policy_supports_wildcard_subdomains(monkeypatch):
    from app.services.target_policy import validate_target, TargetPolicyError
    monkeypatch.setattr(config, "ALLOWED_TARGET_HOSTS", ("*.corp.example.com",))
    monkeypatch.setattr(config, "ALLOWED_TARGET_PORTS", (443,))
    validate_target("sapqa.corp.example.com", 443)
    try:
        validate_target("corp.example.com", 443)
    except TargetPolicyError:
        pass
    else:
        raise AssertionError("Wildcard must not match the DNS apex")


def test_check_records_policy_block_without_alert(monkeypatch, tmp_path):
    setup_test_db(monkeypatch, tmp_path)
    from app.services import checks
    from app.services.target_policy import TargetPolicyError

    monkeypatch.setattr(checks, "ALERT_WEBHOOK_URL", "https://hooks.example.test")
    sent = []
    monkeypatch.setattr(checks, "send_webhook", lambda payload: (sent.append(payload) or True, None))
    headers = {"X-API-Key": "test-secret"}
    with TestClient(app) as client:
        target = client.post(
            "/api/v1/targets", json={"name": "SAP QA", "hostname": "qa.example.test"},
            headers=headers,
        ).json()
        def blocked(host, port):
            raise TargetPolicyError("blocked by outbound target policy")
        monkeypatch.setattr(checks, "inspect_certificate", blocked)
        response = client.post(f"/api/v1/targets/{target['id']}/check", headers=headers)
        assert response.status_code == 200
        assert response.json()["status"] == "blocked"
        assert response.json()["error"] == "blocked by outbound target policy"
        assert sent == []
