"""Configuration snapshot storage, secret redaction and baseline drift analysis."""
import json
import re
from datetime import datetime, timezone

from sqlalchemy import desc, select, update

from ..db import configuration_snapshots, get_db

_SECRET_KEY = re.compile(r"(password|passwd|secret|token|private.?key|credential|api.?key)", re.I)
MAX_SNAPSHOT_BYTES = 512_000


def _is_secret_key(name: str) -> bool:
    # SAP profile parameters such as login/min_password_lng describe policy,
    # not credentials. Preserve those values so security drift remains visible.
    normalized = name.lower().replace(".", "_").replace("/", "_").replace("-", "_")
    policy_parameter = re.search(
        r"(min_password_(lng|len|length)|password_(length|policy|history|expiry|expiration|complexity)|fails_to_user_lock)",
        normalized,
    )
    return bool(_SECRET_KEY.search(name)) and not policy_parameter


def _sanitize(value, depth=0):
    if depth > 12:
        raise ValueError("Configuration nesting exceeds 12 levels")
    if isinstance(value, dict):
        if len(value) > 5000:
            raise ValueError("A configuration object may contain at most 5000 keys")
        clean = {}
        for key, item in value.items():
            name = str(key)[:200]
            clean[name] = "[REDACTED]" if _is_secret_key(name) else _sanitize(item, depth + 1)
        return clean
    if isinstance(value, list):
        if len(value) > 5000:
            raise ValueError("A configuration array may contain at most 5000 items")
        return [_sanitize(item, depth + 1) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError(f"Unsupported configuration value type: {type(value).__name__}")


def _flatten(value, prefix=""):
    result = {}
    if isinstance(value, dict):
        for key in sorted(value):
            path = f"{prefix}.{key}" if prefix else key
            result.update(_flatten(value[key], path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            result.update(_flatten(item, f"{prefix}[{index}]"))
    else:
        result[prefix or "$"] = value
    return result


def _public(row):
    item = dict(row)
    item["snapshot"] = json.loads(item.pop("snapshot_json"))
    for key in ("captured_at",):
        if hasattr(item[key], "isoformat"):
            item[key] = item[key].isoformat()
    item["is_baseline"] = bool(item["is_baseline"])
    return item


def capture(system_id: int, source: str, snapshot: dict, set_baseline: bool = False) -> dict:
    if not isinstance(snapshot, dict) or not snapshot:
        raise ValueError("snapshot must be a non-empty JSON object")
    clean = _sanitize(snapshot)
    encoded = json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise ValueError(f"Snapshot exceeds {MAX_SNAPSHOT_BYTES} bytes")
    now = datetime.now(timezone.utc)
    with get_db() as conn:
        if set_baseline:
            conn.execute(update(configuration_snapshots).where(
                configuration_snapshots.c.system_id == system_id
            ).values(is_baseline=False))
        result = conn.execute(configuration_snapshots.insert().values(
            system_id=system_id, source=source, captured_at=now, is_baseline=set_baseline,
            snapshot_json=encoded, change_count=0,
        ))
        snapshot_id = result.inserted_primary_key[0]
    return {"id": snapshot_id, "system_id": system_id, "source": source,
            "captured_at": now.isoformat(), "is_baseline": set_baseline,
            "redacted": True, "size_bytes": len(encoded.encode("utf-8"))}


def list_snapshots(system_id: int, limit: int = 50) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(select(configuration_snapshots).where(
            configuration_snapshots.c.system_id == system_id
        ).order_by(desc(configuration_snapshots.c.captured_at), desc(configuration_snapshots.c.id)).limit(limit)).mappings().all()
    return [_public(r) for r in rows]


def drift(system_id: int) -> dict:
    with get_db() as conn:
        baseline = conn.execute(select(configuration_snapshots).where(
            configuration_snapshots.c.system_id == system_id,
            configuration_snapshots.c.is_baseline.is_(True)
        ).order_by(desc(configuration_snapshots.c.captured_at), desc(configuration_snapshots.c.id)).limit(1)).mappings().first()
        latest = conn.execute(select(configuration_snapshots).where(
            configuration_snapshots.c.system_id == system_id
        ).order_by(desc(configuration_snapshots.c.captured_at), desc(configuration_snapshots.c.id)).limit(1)).mappings().first()
    if not baseline:
        return {"system_id": system_id, "baseline": None, "latest": _public(latest) if latest else None,
                "status": "baseline_required", "changes": [], "summary": {"added": 0, "removed": 0, "modified": 0}}
    if not latest:
        return {"system_id": system_id, "baseline": _public(baseline), "latest": None,
                "status": "no_current_snapshot", "changes": [], "summary": {"added": 0, "removed": 0, "modified": 0}}
    before = _flatten(json.loads(baseline["snapshot_json"]))
    after = _flatten(json.loads(latest["snapshot_json"]))
    changes = []
    for path in sorted(set(before) | set(after)):
        if path not in before:
            kind, old, new = "added", None, after[path]
        elif path not in after:
            kind, old, new = "removed", before[path], None
        elif before[path] != after[path]:
            kind, old, new = "modified", before[path], after[path]
        else:
            continue
        changes.append({"path": path, "change": kind, "baseline_value": old, "current_value": new})
    summary = {kind: sum(1 for c in changes if c["change"] == kind) for kind in ("added", "removed", "modified")}
    same = baseline["id"] == latest["id"] or not changes
    return {"system_id": system_id, "baseline": _public(baseline), "latest": _public(latest),
            "status": "compliant" if same else "drift_detected", "changes": changes, "summary": summary}
