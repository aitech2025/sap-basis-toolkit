"""SAP HANA / database table growth watchdog.

Ingest periodic size snapshots (DB02, HANA M_CS_TABLES / M_TABLE_PERSISTENCE_STATISTICS
exports, or any CSV with table name + size), compute growth rates, forecast when disk
or memory reaches the configured threshold and recommend standard housekeeping.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .. import config
from ..db import capacity_snapshots, get_db, table_size_snapshots
from .alerts import send_alert_once
from .tabular import InputFormatError, parse_number, pick, read_rows, require_columns

# Typical Basis housekeeping. Always validate against your release and retention policy.
HOUSEKEEPING = {
    "BALHDR": "Application log: delete expired logs with SLG2 / report SBAL_DELETE (schedule daily).",
    "BALDAT": "Application log data: delete with SLG2 / SBAL_DELETE; check log expiry dates set by applications.",
    "BALC": "Application log context: cleaned together with BALHDR/BALDAT via SLG2.",
    "SOFFCONT1": "SAPoffice/BCS document content: reorganise with RSBCS_REORG and move attachments to a content server.",
    "SOC3": "SAPoffice content: reorganise with RSBCS_REORG / SAPoffice reorg reports.",
    "EDIDC": "IDoc control records: archive with archiving object IDOC (SARA); define retention per message type.",
    "EDIDS": "IDoc status records: archive with archiving object IDOC (SARA).",
    "EDI40": "IDoc data records: archive with archiving object IDOC (SARA).",
    "DBTABLOG": "Table change logging: delete old logs with RSTBPDEL (SCU3); review rec/client logging scope.",
    "CDHDR": "Change documents: archive with archiving object CHANGEDOCU.",
    "CDPOS": "Change document items: archive with archiving object CHANGEDOCU.",
    "TST03": "Spool data: schedule RSPO1041 (or RSPO0041) spool reorganisation.",
    "TSP01": "Spool requests: schedule RSPO1041 spool reorganisation.",
    "APQD": "Batch input data: delete processed sessions with RSBDCREO.",
    "APQI": "Batch input queue: delete processed sessions with RSBDCREO.",
    "TBTCO": "Background job headers: schedule RSBTCDEL2 to delete old job logs.",
    "TBTCP": "Background job steps: schedule RSBTCDEL2.",
    "BDCP2": "Change pointers: delete processed pointers with RBDCPCLR2.",
    "ARFCSDATA": "tRFC payload: investigate stuck LUWs in SM58 before cleanup; do not delete blindly.",
    "SWWLOGHIST": "Workflow logs: archive with archiving object WORKITEM.",
    "SWWWIHEAD": "Workflow work items: archive with archiving object WORKITEM.",
    "SXMSCLUP": "PI/XI message payloads: verify archiving/deletion jobs (SXMS_DELETE / retention in SXMB_ADM).",
    "SXMSCLUR": "PI/XI message payloads: verify archiving/deletion jobs and retention in SXMB_ADM.",
    "VBDATA": "Update records: investigate failed updates in SM13; never delete without functional sign-off.",
    "RSDDSTATLOGGING": "BW statistics: delete old statistics data (RSDDSTAT_DATA_DELETE).",
}

TABLE_ALIASES = ("TABLE_NAME", "TABLE", "TABNAME", "Table Name", "Object", "Name")
SIZE_ALIASES = {
    "mb": ("SIZE_MB", "Size (MB)", "Size MB", "Total Size (MB)", "MEMORY_SIZE_MB", "DISK_SIZE_MB"),
    "kb": ("SIZE_KB", "Size (KB)", "Size KB", "Total Size (KB)"),
    "bytes": ("MEMORY_SIZE_IN_TOTAL", "DISK_SIZE", "SIZE_BYTES", "TABLE_SIZE", "Size (Bytes)"),
}
ROW_ALIASES = ("RECORD_COUNT", "ROWS", "ROW_COUNT", "Records", "Number of Rows", "Entries")
DIVISOR = {"mb": 1.0, "kb": 1024.0, "bytes": 1024.0 * 1024.0}


def parse_snapshot(raw: bytes) -> list[dict]:
    rows = read_rows(raw)
    all_size_aliases = tuple(a for group in SIZE_ALIASES.values() for a in group)
    require_columns(rows, {"table name": TABLE_ALIASES, "size": all_size_aliases})
    parsed = {}
    for row in rows:
        table = pick(row, *TABLE_ALIASES).upper()
        size_mb = None
        for unit, aliases in SIZE_ALIASES.items():
            value = parse_number(pick(row, *aliases))
            if value is not None:
                size_mb = value / DIVISOR[unit]
                break
        if not table or size_mb is None:
            continue
        rows_value = parse_number(pick(row, *ROW_ALIASES))
        # HANA exports list one row per partition/host: aggregate per table.
        entry = parsed.setdefault(table, {"table_name": table, "size_mb": 0.0, "row_count": None})
        entry["size_mb"] += size_mb
        if rows_value is not None:
            entry["row_count"] = int((entry["row_count"] or 0) + rows_value)
    if not parsed:
        raise InputFormatError("No usable rows: every row lacked a table name or numeric size")
    return list(parsed.values())


def store_snapshot(sap_system_id: str, raw: bytes, captured_at: datetime | None = None) -> dict:
    captured_at = captured_at or datetime.now(timezone.utc)
    entries = parse_snapshot(raw)
    with get_db() as connection:
        connection.execute(table_size_snapshots.insert(), [
            {**entry, "sap_system_id": sap_system_id, "captured_at": captured_at} for entry in entries
        ])
    return {"sap_system_id": sap_system_id, "tables": len(entries), "captured_at": captured_at.isoformat(),
            "total_size_mb": round(sum(e["size_mb"] for e in entries), 2)}


def store_capacity(sap_system_id: str, disk_total_mb: float, disk_used_mb: float,
                   memory_total_mb: float | None = None, memory_used_mb: float | None = None,
                   captured_at: datetime | None = None) -> dict:
    captured_at = captured_at or datetime.now(timezone.utc)
    values = dict(sap_system_id=sap_system_id, disk_total_mb=disk_total_mb, disk_used_mb=disk_used_mb,
                  memory_total_mb=memory_total_mb, memory_used_mb=memory_used_mb, captured_at=captured_at)
    with get_db() as connection:
        connection.execute(capacity_snapshots.insert().values(**values))
    return {**values, "captured_at": captured_at.isoformat()}


def _aware(value) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _slope_per_day(points: list[tuple[datetime, float]]) -> float | None:
    """Least-squares growth in units/day; robust to irregular snapshot spacing."""
    if len(points) < 2:
        return None
    t0 = points[0][0]
    xs = [(t - t0).total_seconds() / 86400 for t, _ in points]
    ys = [v for _, v in points]
    n = len(xs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator


def _days_until(current: float, limit: float, rate: float | None) -> float | None:
    if rate is None or rate <= 0:
        return None
    return max(0.0, (limit - current) / rate)


def build_report(sap_system_id: str, window_days: int = 30, top: int = 10) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    with get_db() as connection:
        rows = connection.execute(
            select(table_size_snapshots).where(
                table_size_snapshots.c.sap_system_id == sap_system_id,
                table_size_snapshots.c.captured_at >= since,
            ).order_by(table_size_snapshots.c.captured_at)
        ).mappings().all()
        capacity = connection.execute(
            select(capacity_snapshots).where(
                capacity_snapshots.c.sap_system_id == sap_system_id,
                capacity_snapshots.c.captured_at >= since,
            ).order_by(capacity_snapshots.c.captured_at)
        ).mappings().all()

    series: dict[str, list[tuple[datetime, float, int | None]]] = {}
    for row in rows:
        series.setdefault(row["table_name"], []).append((_aware(row["captured_at"]), row["size_mb"], row["row_count"]))

    tables = []
    for name, points in series.items():
        latest_at, latest_size, latest_rows = points[-1]
        first_at, first_size, _ = points[0]
        rate = _slope_per_day([(t, s) for t, s, _ in points])
        tables.append({
            "table_name": name,
            "size_mb": round(latest_size, 2),
            "row_count": latest_rows,
            "snapshots": len(points),
            "growth_mb_per_day": round(rate, 3) if rate is not None else None,
            "growth_mb_window": round(latest_size - first_size, 2),
            "growth_pct_window": round((latest_size - first_size) / first_size * 100, 1) if first_size else None,
            "recommendation": HOUSEKEEPING.get(name),
        })
    tables.sort(key=lambda t: (t["growth_mb_per_day"] or 0, t["size_mb"]), reverse=True)

    total_rate = sum(t["growth_mb_per_day"] for t in tables if t["growth_mb_per_day"] and t["growth_mb_per_day"] > 0)
    forecast = None
    if capacity:
        latest = capacity[-1]
        threshold_pct = config.GROWTH_CAPACITY_THRESHOLD_PCT
        disk_points = [(_aware(c["captured_at"]), c["disk_used_mb"]) for c in capacity]
        disk_rate = _slope_per_day(disk_points) if len(disk_points) >= 2 else (total_rate or None)
        disk_limit = latest["disk_total_mb"] * threshold_pct / 100
        forecast = {
            "captured_at": _aware(latest["captured_at"]).isoformat(),
            "threshold_pct": threshold_pct,
            "disk_used_pct": round(latest["disk_used_mb"] / latest["disk_total_mb"] * 100, 1),
            "disk_growth_mb_per_day": round(disk_rate, 2) if disk_rate else None,
            "days_until_disk_threshold": _round(_days_until(latest["disk_used_mb"], disk_limit, disk_rate)),
            "days_until_disk_full": _round(_days_until(latest["disk_used_mb"], latest["disk_total_mb"], disk_rate)),
        }
        if latest["memory_total_mb"] and latest["memory_used_mb"] is not None:
            mem_points = [(_aware(c["captured_at"]), c["memory_used_mb"]) for c in capacity if c["memory_used_mb"] is not None]
            mem_rate = _slope_per_day(mem_points)
            forecast.update({
                "memory_used_pct": round(latest["memory_used_mb"] / latest["memory_total_mb"] * 100, 1),
                "memory_growth_mb_per_day": round(mem_rate, 2) if mem_rate else None,
                "days_until_memory_threshold": _round(_days_until(
                    latest["memory_used_mb"], latest["memory_total_mb"] * threshold_pct / 100, mem_rate)),
            })

    return {
        "sap_system_id": sap_system_id,
        "window_days": window_days,
        "tracked_tables": len(tables),
        "tracked_growth_mb_per_day": round(total_rate, 2),
        "capacity_forecast": forecast,
        "top_growing_tables": tables[:top],
        "housekeeping_candidates": [t for t in tables if t["recommendation"] and (t["growth_mb_per_day"] or 0) > 0][:top],
    }


def _round(value):
    return round(value, 1) if value is not None else None


def known_systems() -> list[str]:
    with get_db() as connection:
        a = connection.execute(select(table_size_snapshots.c.sap_system_id).distinct()).scalars().all()
        b = connection.execute(select(capacity_snapshots.c.sap_system_id).distinct()).scalars().all()
    return sorted(set(a) | set(b))


def evaluate_growth_alerts() -> list[dict]:
    """Alert when disk or memory is forecast to cross the threshold within GROWTH_ALERT_DAYS."""
    raised = []
    week = datetime.now(timezone.utc).strftime("%G-W%V")
    for system in known_systems():
        report = build_report(system)
        forecast = report["capacity_forecast"] or {}
        for resource in ("disk", "memory"):
            days = forecast.get(f"days_until_{resource}_threshold")
            if days is None or days > config.GROWTH_ALERT_DAYS:
                continue
            severity = "critical" if days <= 7 else "warning"
            top = ", ".join(t["table_name"] for t in report["top_growing_tables"][:5])
            send_alert_once(
                "table-growth", f"growth:{system}:{resource}:{severity}:{week}",
                f"{system}: {resource} forecast to reach {forecast['threshold_pct']}% in {days} days",
                severity,
                {"sap_system_id": system, f"{resource}_used_pct": forecast.get(f"{resource}_used_pct"),
                 "fastest_growing_tables": top},
            )
            raised.append({"sap_system_id": system, "resource": resource, "days": days, "severity": severity})
    return raised
