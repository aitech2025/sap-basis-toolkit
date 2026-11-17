from datetime import datetime, timedelta, timezone

from app import db
from app.services import monitors


def test_metric_points_write_query_and_retention(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DATABASE_URL", f"sqlite:///{tmp_path / 'timeseries.db'}")
    monkeypatch.setattr("app.config.METRIC_RETENTION_DAYS", 1)
    db.reset_engine_for_tests()
    db.init_db()

    with db.get_db() as connection:
        system_id = connection.execute(db.sap_systems.insert().values(
            sid="TST", name="Timeseries Test", product="S4HANA", stack="ABAP",
            database="HANA", environment="DEV", landscape="test",
        ).returning(db.sap_systems.c.id)).scalar_one()

    now = datetime.now(timezone.utc)
    monitors.record_metrics(system_id, {"cpu.utilization_pct": 42.5, "hana.memory_used_pct": 68.0}, now)
    result = monitors.series(system_id, ["cpu.utilization_pct", "hana.memory_used_pct"], hours=1)

    assert result["cpu.utilization_pct"] == [{"ts": now.isoformat(), "value": 42.5}]
    assert result["hana.memory_used_pct"] == [{"ts": now.isoformat(), "value": 68.0}]

    old = now - timedelta(days=2)
    with db.get_db() as connection:
        connection.execute(db.metric_points.insert().values(
            system_id=system_id, metric="cpu.utilization_pct", value=10.0, ts=old,
        ))
    monitors.prune()
    after_prune = monitors.series(system_id, ["cpu.utilization_pct"], hours=24)
    assert len(after_prune["cpu.utilization_pct"]) == 1
    db.reset_engine_for_tests()
