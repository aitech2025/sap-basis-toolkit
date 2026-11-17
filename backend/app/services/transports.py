"""Automated pre- and post-import transport checker (stateless analysis).

Pre-import inputs (SE16 exports from the source/DEV system):
* E071  – object list: TRKORR, PGMID, OBJECT, OBJ_NAME, optional OBJFUNC
* E070  – optional header: TRKORR, TRSTATUS, AS4DATE, AS4TIME, AS4USER, STRKORR
* queue – optional ordered list of requests to import (defaults to all requests by release time)
* imported_in_target – optional list of requests already imported into the target (STMS history)

Post-import input: STMS import history / TPALOG export (TRKORR + RC) or raw tp/STMS log text.
"""
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .. import config
from .alerts import send_alert_once
from .tabular import parse_sap_datetime, pick, read_rows, require_columns

TR_PATTERN = re.compile(r"\b([A-Z0-9]{3}K\d{6})\b")
RC_PATTERN = re.compile(r"(?:\brc\b|return\s*code|exit\s*code)\s*[:=]?\s*\"?(\d{1,4})", re.IGNORECASE)

TABLE_CONTENT_OBJECTS = {"TABU", "TDAT", "VDAT", "CDAT"}
DDIC_STRUCTURE_OBJECTS = {"TABL", "TABD", "TABT", "VIEW", "VIED", "DOMA", "DTEL", "TTYP", "SHLP", "ENQU"}
RELEASED_STATUSES = {"R", "N", "O"}
SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def _requests_from_e070(raw: str | None) -> dict[str, dict]:
    if not raw:
        return {}
    rows = read_rows(raw)
    require_columns(rows, {"request": ("TRKORR", "Request", "Transport Request")})
    headers = {}
    for row in rows:
        tr = pick(row, "TRKORR", "Request", "Transport Request").upper()
        if not tr:
            continue
        headers[tr] = {
            "status": pick(row, "TRSTATUS", "Status").upper()[:1],
            "released_at": f"{pick(row, 'AS4DATE', 'Date')}{pick(row, 'AS4TIME', 'Time')}".replace(".", "").replace(":", "").replace("-", ""),
            "owner": pick(row, "AS4USER", "Owner"),
            "parent": pick(row, "STRKORR", "Parent Request").upper(),
            "function": pick(row, "TRFUNCTION", "Type").upper()[:1],
        }
    return headers


def _sortable_release_key(released_at: str) -> str:
    """Normalise to YYYYMMDDhhmmss when E070 used DD.MM.YYYY or ISO formats."""
    digits = re.sub(r"\D", "", released_at)
    if len(digits) >= 8 and not digits.startswith(("19", "20")) and digits[4:6] in ("19", "20"):
        digits = digits[4:8] + digits[2:4] + digits[0:2] + digits[8:]
    return digits.ljust(14, "0")


def precheck(e071_csv: str, e070_csv: str | None = None, queue: list[str] | None = None,
             imported_in_target: list[str] | None = None, approved_tables: list[str] | None = None) -> dict:
    rows = read_rows(e071_csv)
    require_columns(rows, {
        "request": ("TRKORR", "Request"),
        "object type": ("OBJECT", "Object Type"),
        "object name": ("OBJ_NAME", "Object Name"),
    })
    headers = _requests_from_e070(e070_csv)
    approved = {t.strip().upper() for t in (approved_tables or []) if t.strip()}
    imported = {t.strip().upper() for t in (imported_in_target or []) if t.strip()}

    objects_by_tr: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        tr = pick(row, "TRKORR", "Request").upper()
        parent = headers.get(tr, {}).get("parent")
        owner_tr = parent or tr  # tasks roll up to their request
        objects_by_tr[owner_tr].append({
            "pgmid": pick(row, "PGMID", "Program ID", default="R3TR").upper(),
            "object": pick(row, "OBJECT", "Object Type").upper(),
            "name": pick(row, "OBJ_NAME", "Object Name").upper(),
            "objfunc": pick(row, "OBJFUNC", "Function").upper(),
        })

    def release_key(tr: str) -> str:
        return _sortable_release_key(headers.get(tr, {}).get("released_at", "")) + tr

    if queue:
        ordered = [q.strip().upper() for q in queue if q.strip()]
    else:
        ordered = sorted(objects_by_tr, key=release_key)
    position = {tr: i for i, tr in enumerate(ordered)}

    findings = []

    def add(severity, code, request, message, **extra):
        findings.append({"severity": severity, "code": code, "request": request, "message": message, **extra})

    # Request-level checks
    for tr in ordered:
        if tr not in objects_by_tr:
            add("warning", "EMPTY_OR_UNKNOWN_REQUEST", tr, "Request has no objects in the E071 export")
        status = headers.get(tr, {}).get("status")
        if headers and status and status not in RELEASED_STATUSES:
            add("error", "NOT_RELEASED", tr, f"Request status '{status}' is not released")
        if tr in imported:
            add("info", "ALREADY_IMPORTED", tr, "Request is already in the target import history (re-import)")

    # Object-level checks
    holders: dict[tuple[str, str], list[str]] = defaultdict(list)
    for tr, objs in objects_by_tr.items():
        for obj in objs:
            key = (obj["object"], obj["name"])
            if tr not in holders[key]:
                holders[key].append(tr)
            if tr not in position:
                continue
            if obj["object"] in TABLE_CONTENT_OBJECTS:
                table = obj["name"].split()[0] if obj["object"] == "TABU" else obj["name"]
                if table in config.PROTECTED_TABLES and table not in approved:
                    add("error", "UNAPPROVED_PROTECTED_TABLE", tr,
                        f"Table content for protected table {table} without approval", object=f"{obj['object']} {obj['name']}")
                else:
                    add("warning" if table not in approved else "info", "TABLE_CONTENT", tr,
                        f"Transports table content ({obj['object']} {obj['name']}); confirm customizing approval",
                        object=f"{obj['object']} {obj['name']}")
            if obj["object"] in DDIC_STRUCTURE_OBJECTS and obj["object"] in ("TABL", "TABD"):
                add("warning", "DDIC_TABLE_CHANGE", tr,
                    f"Dictionary table definition {obj['name']} changes; large tables may need conversion (SE14) and downtime",
                    object=f"{obj['object']} {obj['name']}")
            if obj["objfunc"] == "D":
                add("warning", "OBJECT_DELETION", tr, f"Deletes {obj['object']} {obj['name']} in target",
                    object=f"{obj['object']} {obj['name']}")

    queued_set = set(ordered)
    for (obj_type, name), trs in holders.items():
        in_queue = [tr for tr in trs if tr in queued_set]
        label = f"{obj_type} {name}"
        if len(in_queue) > 1:
            in_queue.sort(key=position.get)
            add("warning", "OBJECT_COLLISION", in_queue[-1],
                f"{label} is contained in {len(in_queue)} queued requests: {', '.join(in_queue)}",
                object=label, requests=in_queue)
            if headers:
                for earlier, later in zip(in_queue, in_queue[1:]):
                    if release_key(later) < release_key(earlier):
                        add("error", "SEQUENCE_INVERSION", later,
                            f"{later} carries an OLDER version of {label} than {earlier} but is imported after it; "
                            "the newer version would be overwritten", object=label, requests=[earlier, later])
        if imported and in_queue:
            first_queued = min(in_queue, key=release_key)
            predecessors = [
                tr for tr in trs
                if tr not in queued_set and tr not in imported
                and headers.get(tr, {}).get("status") in RELEASED_STATUSES
                and release_key(tr) < release_key(first_queued)
            ]
            if predecessors:
                add("warning", "MISSING_PREDECESSOR", first_queued,
                    f"{label} was changed earlier in {', '.join(predecessors)} which is not imported in the target "
                    "and not in this queue (possible missing dependency)", object=label, requests=predecessors)

    findings.sort(key=lambda f: (SEVERITY_ORDER[f["severity"]], position.get(f["request"], 1 << 30), f["code"]))
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITY_ORDER}
    verdict = "block" if counts["error"] else "review" if counts["warning"] else "pass"
    notes = []
    if not headers:
        notes.append("No E070 export supplied: release status and sequence-inversion checks were skipped.")
    if not imported:
        notes.append("No target import history supplied: missing-predecessor check was skipped.")
    return {
        "verdict": verdict,
        "counts": counts,
        "queue": [{"request": tr, "objects": len(objects_by_tr.get(tr, [])),
                   "owner": headers.get(tr, {}).get("owner")} for tr in ordered],
        "findings": findings,
        "notes": notes,
    }


def classify_rc(rc: int) -> str:
    if rc == 0:
        return "success"
    if rc <= 4:
        return "warning"
    if rc <= 8:
        return "error"
    return "fatal"


def parse_import_results(content: str) -> dict[str, int]:
    """Return the maximum RC per request from a CSV export or raw log text."""
    results: dict[str, int] = {}
    first_line = content.strip().splitlines()[0] if content.strip() else ""
    if re.search(r"TRKORR|REQUEST", first_line, re.IGNORECASE) and re.search(r"\bRC\b|RETCODE|RETURN", first_line, re.IGNORECASE):
        for row in read_rows(content):
            tr = pick(row, "TRKORR", "Request", "Transport Request").upper()
            rc_raw = pick(row, "RC", "RETCODE", "Return Code", "MAXRC").strip()
            if tr and rc_raw.isdigit():
                results[tr] = max(results.get(tr, 0), int(rc_raw))
        return results
    for line in content.splitlines():
        trs = TR_PATTERN.findall(line)
        rc_match = RC_PATTERN.search(line)
        if trs and rc_match:
            rc = int(rc_match.group(1))
            for tr in trs:
                results[tr] = max(results.get(tr, 0), rc)
    return results


def postcheck(content: str, target_system: str, alert: bool = True, system_id: int | None = None) -> dict:
    results = parse_import_results(content)
    entries = [{"request": tr, "rc": rc, "result": classify_rc(rc)} for tr, rc in sorted(results.items())]
    failed = [e for e in entries if e["rc"] >= 8]
    alerts = []
    if alert:
        for entry in failed:
            severity = "critical" if entry["rc"] >= 12 else "error"
            alerts.extend(send_alert_once(
                "transports", f"import:{system_id or target_system}:{entry['request']}:{entry['rc']}",
                f"{target_system}: import of {entry['request']} ended with RC {entry['rc']}",
                severity, {"target_system": target_system, "request": entry["request"], "rc": entry["rc"],
                           "next_step": "Check the import log in STMS (Goto > Import History) and SE01 log"},
                system_id=system_id,
            ))
    return {
        "target_system": target_system,
        "requests": len(entries),
        "failed": len(failed),
        "warnings": sum(1 for e in entries if e["result"] == "warning"),
        "results": entries,
        "alerts": alerts,
    }


# ---------------------------------------------------------------- import history
TR_ALIASES = ("TRKORR", "Request", "Transport Request", "Number")
RC_ALIASES = ("RC", "RETCODE", "Return Code", "MAXRC", "Max. RC")
DATE_ALIASES = ("TRTIME", "Import Date", "Date", "AS4DATE", "TRDATE", "Imported On")
TIME_ALIASES = ("Import Time", "Time", "AS4TIME")
OWNER_ALIASES = ("Owner", "AS4USER", "TRUSER", "User")
TEXT_ALIASES = ("Short Text", "AS4TEXT", "Description", "Text")


def parse_import_history(content: str, zone, default_time: datetime) -> list[dict]:
    """Rows of (request, rc, imported_at, owner, description) from STMS/TPALOG CSV or raw tp log text."""
    entries: dict[tuple, dict] = {}
    first_line = content.strip().splitlines()[0] if content.strip() else ""
    is_table = re.search(r"TRKORR|REQUEST|NUMBER", first_line, re.IGNORECASE) and \
        re.search(r"\bRC\b|RETCODE|RETURN|MAXRC", first_line, re.IGNORECASE)
    if is_table:
        for row in read_rows(content):
            tr = pick(row, *TR_ALIASES).upper()
            rc_raw = pick(row, *RC_ALIASES).strip()
            if not TR_PATTERN.fullmatch(tr) or not rc_raw.isdigit():
                continue
            when = parse_sap_datetime(pick(row, *DATE_ALIASES), pick(row, *TIME_ALIASES), zone)
            key = (tr, when)
            entry = entries.setdefault(key, {
                "trkorr": tr, "rc": 0, "imported_at": when or default_time, "time_known": when is not None,
                "owner": pick(row, *OWNER_ALIASES) or None, "description": pick(row, *TEXT_ALIASES) or None,
            })
            entry["rc"] = max(entry["rc"], int(rc_raw))  # several tp steps per import: keep the worst
    else:
        for tr, rc in parse_import_results(content).items():
            entries[(tr, None)] = {"trkorr": tr, "rc": rc, "imported_at": default_time, "time_known": False,
                                   "owner": None, "description": None}
    for entry in entries.values():
        entry["result"] = classify_rc(entry["rc"])
    return sorted(entries.values(), key=lambda e: (e["imported_at"], e["trkorr"]))


def store_import_history(system_id: int, entries: list[dict]) -> dict:
    """Insert new history rows; an identical (request, time, rc) row is not stored twice."""
    from sqlalchemy import select

    from ..db import get_db, transport_imports

    added = 0
    with get_db() as connection:
        for e in entries:
            query = select(transport_imports.c.id).where(
                transport_imports.c.system_id == system_id, transport_imports.c.trkorr == e["trkorr"],
                transport_imports.c.rc == e["rc"])
            if e["time_known"]:
                query = query.where(transport_imports.c.imported_at == e["imported_at"])
            else:
                # untimed log uploads: dedupe against the same request/RC uploaded within the last day
                query = query.where(transport_imports.c.time_known.is_(False),
                                    transport_imports.c.imported_at >= e["imported_at"] - timedelta(days=1))
            if connection.execute(query.limit(1)).first():
                continue
            connection.execute(transport_imports.insert().values(system_id=system_id, **e))
            added += 1
    return {"stored": added, "duplicates": len(entries) - added}


def import_history(system_id: int, days: int = 90, limit: int = 200, result: str | None = None) -> dict:
    from sqlalchemy import select

    from ..db import get_db, transport_imports

    since = datetime.now(timezone.utc) - timedelta(days=days)
    base = select(transport_imports).where(transport_imports.c.system_id == system_id,
                                           transport_imports.c.imported_at >= since)
    with get_db() as connection:
        rows = [dict(r) for r in connection.execute(
            base.order_by(transport_imports.c.imported_at.desc(), transport_imports.c.id.desc())).mappings().all()]
    stats = {"imports": len(rows), "requests": len({r["trkorr"] for r in rows})}
    for key in ("success", "warning", "error", "fatal"):
        stats[key] = sum(1 for r in rows if r["result"] == key)
    stats["failed"] = stats["error"] + stats["fatal"]
    if result:
        wanted = {"failed": ("error", "fatal")}.get(result, (result,))
        rows = [r for r in rows if r["result"] in wanted]
    for r in rows:
        for k in ("imported_at", "uploaded_at"):
            if hasattr(r[k], "isoformat"):
                r[k] = (r[k] if r[k].tzinfo else r[k].replace(tzinfo=timezone.utc)).isoformat()
        r["time_known"] = bool(r["time_known"])
    return {"days": days, "stats": stats, "imports": rows[:limit]}


def imported_requests(system_id: int) -> set[str]:
    """Requests imported into the system with RC < 8 (feeds the missing-predecessor check)."""
    from sqlalchemy import select

    from ..db import get_db, transport_imports

    with get_db() as connection:
        return set(connection.execute(select(transport_imports.c.trkorr).where(
            transport_imports.c.system_id == system_id, transport_imports.c.rc < 8)).scalars().all())
