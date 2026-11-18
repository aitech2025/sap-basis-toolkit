"""ABAP push agent: tokens, ingest and the silence watchdog.

The agent (report Z_SENTINEL_PUSH, class ZCL_SENTINEL_AGENT in /abap) runs as a periodic background job inside the SAP
system, reads ST22/SM37/SM13/SM12/SM58/SMQ1/SMQ2/IDoc data with ABAP authorisations, and POSTs one JSON document per run
to /api/v1/agent/ingest with its token. Each push carries the current state over a rolling 24-hour window, so a lost push
loses nothing and repeated rows are de-duplicated here.
"""
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from .. import config
from ..db import agent_tokens, get_db
from . import incidents, monitors, ops, systems
from .collectors import hana, rfc
from .tabular import parse_sap_datetime

TOKEN_PREFIX = "sat_"


class AgentError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _view(row) -> dict:
    return {"id": row["id"], "system_id": row["system_id"], "hint": row["hint"], "created_at": _aware(row["created_at"]).isoformat(),
            "created_by": row["created_by"], "revoked_at": _aware(row["revoked_at"]).isoformat() if row["revoked_at"] else None,
            "last_seen_at": _aware(row["last_seen_at"]).isoformat() if row["last_seen_at"] else None,
            "last_ip": row["last_ip"], "last_agent_version": row["last_agent_version"],
            "last_payload_bytes": row["last_payload_bytes"]}


def issue_token(system_id: int, actor: str) -> dict:
    """New token for a system; any earlier token of the system is revoked. The plain token is returned only here."""
    systems.get_system(system_id)
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    now = _now()
    with get_db() as connection:
        connection.execute(update(agent_tokens).where(agent_tokens.c.system_id == system_id,
                                                      agent_tokens.c.revoked_at.is_(None)).values(revoked_at=now))
        row = connection.execute(agent_tokens.insert().values(
            system_id=system_id, token_hash=_hash(token), hint=token[:10], created_at=now, created_by=actor[:80])
            .returning(agent_tokens)).mappings().one()
    return {**_view(row), "token": token}


def list_tokens(system_id: int) -> list[dict]:
    with get_db() as connection:
        rows = connection.execute(select(agent_tokens).where(agent_tokens.c.system_id == system_id)
                                  .order_by(agent_tokens.c.created_at.desc())).mappings().all()
    return [_view(r) for r in rows]


def revoke(system_id: int, token_id: int) -> None:
    with get_db() as connection:
        done = connection.execute(update(agent_tokens).where(
            agent_tokens.c.id == token_id, agent_tokens.c.system_id == system_id, agent_tokens.c.revoked_at.is_(None))
            .values(revoked_at=_now())).rowcount
    if not done:
        raise AgentError("No active token with this id", 404)


def authenticate(token: str | None) -> dict:
    if not token or not token.startswith(TOKEN_PREFIX) or len(token) > 100:
        raise AgentError("Missing or malformed agent token", 401)
    with get_db() as connection:
        row = connection.execute(select(agent_tokens).where(agent_tokens.c.token_hash == _hash(token))).mappings().first()
    if row is None or row["revoked_at"] is not None:
        raise AgentError("Unknown or revoked agent token", 401)
    return dict(row)


def _upper(rows) -> list[dict]:
    return [{str(k).upper(): ("" if v is None else v) for k, v in r.items()} for r in rows or [] if isinstance(r, dict)]


def _lower_keys(payload: dict) -> dict:
    return {str(k).lower(): v for k, v in payload.items()}


def map_payload(payload: dict, zone) -> dict:
    """Agent JSON (schema 1) -> the same monitor inputs the HANA SQL and RFC collectors produce."""
    p = _lower_keys(payload)
    raw: dict = {}
    if p.get("dump_log"):
        try:
            log = json.loads(p["dump_log"]) if isinstance(p["dump_log"], str) else p["dump_log"]
        except ValueError as exc:
            raise AgentError(f"dump_log is not valid JSON: {exc}") from exc
        log = {str(k).upper(): v for k, v in log.items()}
        raw["dumps"] = rfc.parse_dump_log({"ET_E2E_LOG": _upper(log.get("ET_E2E_LOG")),
                                           "ES_E2E_LOG_STRUCT_DESC": {str(k).upper(): v for k, v in
                                                                      (log.get("ES_E2E_LOG_STRUCT_DESC") or {}).items()}}, zone)
    elif "dumps" in p:  # SNAP headers (no ST-PI): time, server, user and client, but no runtime error name
        raw["dumps"] = [{"at": parse_sap_datetime(str(r.get("DATUM")), str(r.get("UZEIT")), zone), "error": "not reported (no ST-PI)",
                         "user": r.get("UNAME") or None, "host": r.get("AHOST") or None, "client": r.get("MANDT") or None}
                        for r in _upper(p["dumps"])]
        raw["dumps"] = [d for d in raw["dumps"] if d["at"]]
    tables = {}
    for key in ("updates", "trfc", "qrfc_out", "qrfc_in", "idoc_errors", "jobs", "number_ranges", "applog_errors",
                "batch_input", "clients", "critical_users", "failed_logon_locks", "imports"):
        if key in p:
            tables[key] = _upper(p[key])
    if "spool_requests" in p:
        tables["spool_requests"] = [{"TOTAL": int(p.get("spool_requests") or 0)}]
    if "idoc_errors" in p:
        tables["idoc_total"] = [{"TOTAL": int(p.get("idoc_total") or 0)}]
    raw.update(hana.map_abap_results(tables, zone))
    if "locks" in p:
        now = _now()
        locks = []
        for r in _upper(p["locks"]):
            at = parse_sap_datetime(str(r.get("GTDATE") or ""), str(r.get("GTTIME") or ""), zone) if str(r.get("GTDATE") or "").strip("0 ") else None
            if at and at > now + timedelta(minutes=5):
                at = None
            locks.append({"client": str(r.get("GCLIENT") or "")[:3] or None, "user_name": str(r.get("GUNAME") or "?")[:40],
                          "lock_mode": str(r.get("GMODE") or "")[:2] or None, "table_name": str(r.get("GNAME") or "?").upper()[:64],
                          "argument": str(r.get("GARG") or "").strip() or None, "transaction": str(r.get("GTCODE") or "") or None,
                          "locked_at": at, "age_minutes": max(0, int((now - at).total_seconds() // 60)) if at else None})
        raw["locks"] = locks
    return raw


def ingest(token_row: dict, body: bytes, client_ip: str | None) -> dict:
    from .collectors.runner import emit_abap

    if len(body) > config.AGENT_MAX_BYTES:
        raise AgentError(f"Payload too large ({len(body)} bytes, limit {config.AGENT_MAX_BYTES})", 413)
    now = _now()
    last = _aware(token_row["last_seen_at"])
    if last and (now - last).total_seconds() < config.AGENT_MIN_INTERVAL_SECONDS:
        raise AgentError(f"Pushed too often: wait {config.AGENT_MIN_INTERVAL_SECONDS} seconds between pushes", 429)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise AgentError(f"Body is not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise AgentError("Body must be a JSON object")
    p = _lower_keys(payload)
    if int(p.get("schema") or 0) != 1:
        raise AgentError("Unsupported payload schema (expected 1); update Sentinel or the agent")
    system = systems.get_system(token_row["system_id"])
    sid = str(p.get("sid") or "").strip().upper()
    if sid != system["sid"]:
        raise AgentError(f"Token belongs to {system['sid']}, payload is from {sid or 'an unknown system'}", 403)
    zone = systems.system_zone(system)
    raw = map_payload(payload, zone)
    metrics: dict[str, float] = {"agent.up": 1.0}
    summary: dict = {}
    emit_abap(system, raw, "abap_agent", now, zone, True, metrics, summary)
    if raw.get("locks") is not None:
        locks = raw["locks"]
        ages = [lk["age_minutes"] for lk in locks if lk["age_minutes"] is not None]
        metrics.update({"abap.locks": float(len(locks)), "abap.locks_oldest_min": float(max(ages)) if ages else 0.0,
                        "abap.locks_long": float(sum(1 for a in ages if a >= config.LOCK_WARNING_MINUTES))})
        last_snap = ops.last_lock_snapshot_at(system["id"])
        if last_snap is None or now - last_snap >= timedelta(seconds=config.LOCK_SNAPSHOT_SECONDS):
            kept = [lk for lk in locks if lk["age_minutes"] is None or lk["age_minutes"] >= config.LOCK_STORE_MIN_AGE_MINUTES]
            summary["locks_snapshot"] = ops.store_lock_list(system, kept, now, total=len(locks))
    monitors.record_metrics(system["id"], metrics, now)
    evaluation = incidents.evaluate(system, metrics)
    with get_db() as connection:
        connection.execute(update(agent_tokens).where(agent_tokens.c.id == token_row["id"]).values(
            last_seen_at=now, last_ip=(client_ip or "")[:64] or None,
            last_agent_version=str(p.get("agent_version") or "")[:20] or None, last_payload_bytes=len(body)))
    errors = [str(e)[:300] for e in (p.get("errors") or [])][:20]
    return {"accepted": True, "system": system["sid"], "received_at": now.isoformat(),
            "areas": sorted(k for k in raw if raw[k] is not None), "not_collected_in_sap": errors,
            "metrics": len(metrics), "incidents": evaluation}


def watchdog(now: datetime | None = None) -> list[int]:
    """Mark agents that stopped pushing: agent.up = 0 opens the 'ABAP push agent silent' incident."""
    now = now or _now()
    silent_before = now - timedelta(seconds=config.AGENT_SILENT_SECONDS)
    with get_db() as connection:
        rows = connection.execute(select(agent_tokens.c.system_id, agent_tokens.c.last_seen_at).where(
            agent_tokens.c.revoked_at.is_(None), agent_tokens.c.last_seen_at.is_not(None))).all()
    silent = []
    for system_id, last in rows:
        if _aware(last) < silent_before:
            system = systems.get_system(system_id)
            if not system.get("enabled", True):
                continue
            monitors.record_metrics(system_id, {"agent.up": 0.0}, now)
            incidents.evaluate(system, {"agent.up": 0.0})
            silent.append(system_id)
    return silent
