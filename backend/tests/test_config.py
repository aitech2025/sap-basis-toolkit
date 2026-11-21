import pytest

from app import config


def test_production_rejects_placeholder_secrets(monkeypatch):
    monkeypatch.setattr(config, "APP_ENV", "production")
    monkeypatch.setattr(config, "SECRET_KEY", "replace-with-a-long-random-secret-value")
    monkeypatch.setattr(config, "API_AUTH_TOKEN", "replace-with-a-long-random-api-token")
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql+psycopg://sentinel:secret@db/sentinel")
    monkeypatch.setattr(config, "ALLOWED_TARGET_HOSTS", ("sap.example.test",))
    monkeypatch.setattr(config, "SIMULATOR_ENABLED", False)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        config.validate_runtime_config()


def test_production_rejects_sqlite_and_simulator(monkeypatch):
    monkeypatch.setattr(config, "APP_ENV", "production")
    monkeypatch.setattr(config, "SECRET_KEY", "s" * 64)
    monkeypatch.setattr(config, "API_AUTH_TOKEN", "a" * 64)
    monkeypatch.setattr(config, "DATABASE_URL", "sqlite:///./sentinel.db")
    monkeypatch.setattr(config, "ALLOWED_TARGET_HOSTS", ("sap.example.test",))
    monkeypatch.setattr(config, "SIMULATOR_ENABLED", True)
    with pytest.raises(RuntimeError) as exc:
        config.validate_runtime_config()
    assert "PostgreSQL" in str(exc.value)
    assert "SIMULATOR_ENABLED" in str(exc.value)


def test_production_accepts_valid_baseline(monkeypatch):
    monkeypatch.setattr(config, "APP_ENV", "production")
    monkeypatch.setattr(config, "SECRET_KEY", "s" * 64)
    monkeypatch.setattr(config, "API_AUTH_TOKEN", "a" * 64)
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql+psycopg://sentinel:secret@db/sentinel")
    monkeypatch.setattr(config, "ALLOWED_TARGET_HOSTS", ("sap.example.test",))
    monkeypatch.setattr(config, "SIMULATOR_ENABLED", False)
    config.validate_runtime_config()


def test_development_keeps_local_defaults(monkeypatch):
    monkeypatch.setattr(config, "APP_ENV", "development")
    monkeypatch.setattr(config, "SECRET_KEY", "")
    monkeypatch.setattr(config, "API_AUTH_TOKEN", "")
    monkeypatch.setattr(config, "DATABASE_URL", "sqlite:///./sentinel.db")
    monkeypatch.setattr(config, "ALLOWED_TARGET_HOSTS", ())
    monkeypatch.setattr(config, "SIMULATOR_ENABLED", True)
    config.validate_runtime_config()
