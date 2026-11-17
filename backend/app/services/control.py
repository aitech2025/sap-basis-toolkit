"""Start / stop / restart SAP systems and instances through sapstartsrv.

Safeguards
- Disabled globally with CONTROL_ENABLED=false; per system with ``control_enabled`` (unset means
  enabled for every environment except PRD, which must be switched on explicitly).
- Admin role only (see auth.authorize); the caller must retype the SID and give a reason.
- One running operation per system; every request is audited and kept in system_operations.
- Optional maintenance window so the expected outage does not page anyone.

Execution
- System scope: StartSystem / StopSystem / RestartSystem with an options value such as
  SAPControl-ALL-INSTANCES, which honours the instance start priorities (ASCS before PAS/AAS on start,
  the reverse on stop). Instance scope: Start / Stop / RestartInstance on that instance's sapstartsrv.
- These calls are asynchronous, so a worker thread polls GetSystemInstanceList every
  CONTROL_POLL_SECONDS until every targeted instance reaches the goal state or CONTROL_TIMEOUT_SECONDS.
- sapstartsrv does not start or stop the database (that is SAP Host Agent / HDB); the UI says so.
"""
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from .. import config
from ..db import collectors, get_db, system_operations
from . import incidents, systems
from .collectors import CollectorError, runner
from .discovery import control_client, list_instances

logger = logging.getLogger(__name__)

ACTIONS = ("start", "stop", "restart")
OPTIONS = {"ALL": "SAPControl-ALL-INSTANCES", "SCS": "SAPControl-SCS-INSTANCES", "DIALOG": "SAPControl-DIALOG-INSTANCES",
           "ABAP": "SAPControl-ABAP-INSTANCES", "J2EE": "SAPControl-J2EE-INSTANCES"}
OPTION_FEATURES = {"ALL": None, "SCS": {"MESSAGESERVER", "ENQUE", "ENQREP"}, "DIALOG": {"ABAP", "J2EE"},
                   "ABAP": {"ABAP"}, "J2EE": {"J2EE"}}


class ControlError(ValueError):
    pass


class ControlConflict(ControlError):
    pass


def _iso(v):
    if v is None:
        return None
    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).isoformat()


def view(row) -> dict:
    item = dict(row)
    item["progress"] = json.loads(item["progress"]) if item.get("progress") else []
    for k in ("started_at", "finished_at"):
        item[k] = _iso(item[k])
    return item


def control_allowed(system: dict) -> tuple[bool, str | None]:
    if not config.CONTROL_ENABLED:
        return False, "Start/stop is disabled on this server (CONTROL_ENABLED=false)"
    flag = system.get("control_enabled")
    if flag is None:
        if system.get("environment") == "PRD":
            return False, "Start/stop is off for production systems until an admin enables it in the system settings"
        return True, None
    return (True, None) if flag else (False, "Start/stop is disabled for this system")


def _collector(system_id: int) -> dict:
    with get_db() as connection:
        row = connection.execute(select(collectors).where(collectors.c.system_id == system_id,
                                                          collectors.c.type == "sapcontrol")
                                 .order_by(collectors.c.id)).mappings().first()
    if row is None:
        raise ControlError("No sapcontrol collector: run discovery or configure one under Data collection")
    if not row["username"]:
        raise ControlError("The sapcontrol collector has no OS credentials; start/stop needs <sid>adm or an admin user")
    return dict(row)


def capabilities(system_id: int) -> dict:
    system = systems.get_system(system_id)
    allowed, why = control_allowed(system)
    try:
        _collector(system_id)
        has_creds, cred_msg = True, None
    except ControlError as exc:
        has_creds, cred_msg = False, str(exc)
    return {"allowed": allowed and has_creds, "reason": why or cred_msg, "options": list(OPTIONS),
            "running": running_operation(system_id), "timeout_seconds": config.CONTROL_TIMEOUT_SECONDS,
            "database_note": "sapstartsrv starts/stops SAP instances only; the database is controlled with SAP Host Agent or HDB."}


def running_operation(system_id: int) -> dict | None:
    with get_db() as connection:
        row = connection.execute(select(system_operations).where(system_operations.c.system_id == system_id,
                                                                 system_operations.c.status == "running")).mappings().first()
    return view(row) if row else None


def list_operations(system_id: int, limit: int = 50) -> list[dict]:
    with get_db() as connection:
        rows = connection.execute(select(system_operations).where(system_operations.c.system_id == system_id)
                                  .order_by(system_operations.c.id.desc()).limit(limit)).mappings().all()
    return [view(r) for r in rows]


def get_operation(operation_id: int) -> dict:
    with get_db() as connection:
        row = connection.execute(select(system_operations).where(system_operations.c.id == operation_id)).mappings().first()
    if row is None:
        raise LookupError("Operation not found")
    return view(row)


def _targets(instances: list[dict], scope: str, option: str | None, hostname: str | None, nr: str | None) -> list[dict]:
    if scope == "instance":
        return [i for i in instances if i["hostname"] == hostname and i["instance_nr"] == nr]
    feats = OPTION_FEATURES[option or "ALL"]
    if feats is None:
        return instances
    return [i for i in instances if set((i.get("features") or "").upper().split("|")) & feats]


def request(system_id: int, data: dict, actor: str) -> dict:
    system = systems.get_system(system_id)
    allowed, why = control_allowed(system)
    if not allowed:
        raise ControlError(why)
    action = (data.get("action") or "").lower()
    scope = (data.get("scope") or "system").lower()
    if action not in ACTIONS:
        raise ControlError(f"action must be one of {', '.join(ACTIONS)}")
    if scope not in ("system", "instance"):
        raise ControlError("scope must be system or instance")
    if (data.get("confirm_sid") or "").strip().upper() != system["sid"]:
        raise ControlError(f"Type the SID ({system['sid']}) to confirm")
    reason = (data.get("reason") or "").strip()
    if len(reason) < 5:
        raise ControlError("Give a reason (at least 5 characters); it is recorded in the audit trail")
    option = (data.get("options") or "ALL").upper()
    if scope == "system" and option not in OPTIONS:
        raise ControlError(f"options must be one of {', '.join(OPTIONS)}")
    soft = data.get("soft_timeout")
    soft = int(soft) if soft not in (None, "") else None
    if soft is not None and not 0 <= soft <= 3600:
        raise ControlError("soft_timeout must be between 0 and 3600 seconds")
    hostname = (data.get("hostname") or "").lower() or None
    nr = str(data["instance_nr"]).zfill(2) if data.get("instance_nr") not in (None, "") else None
    instances = list_instances(system_id)
    if scope == "instance":
        if not any(i["hostname"] == hostname and i["instance_nr"] == nr for i in instances):
            raise ControlError("Unknown instance; refresh the instance list first")
    elif not instances:
        raise ControlError("No instances known; refresh the instance list first")
    collector = _collector(system_id)

    window_id = None
    minutes = int(data.get("maintenance_minutes") or 0)
    if minutes:
        if not 5 <= minutes <= 24 * 60:
            raise ControlError("maintenance_minutes must be between 5 and 1440")
        now = datetime.now(timezone.utc)
        window = incidents.create_maintenance({"scope_type": "system", "scope_value": str(system_id), "starts_at": now,
                                               "ends_at": now + timedelta(minutes=minutes),
                                               "reason": f"{action} {system['sid']}: {reason}"[:500]}, actor)
        window_id = window["id"]

    with get_db() as connection:
        # Re-check inside the insert transaction window to keep one running operation per system.
        busy = connection.execute(select(system_operations.c.id).where(system_operations.c.system_id == system_id,
                                                                       system_operations.c.status == "running")).first()
        if busy:
            raise ControlConflict(f"Operation {busy[0]} is still running for {system['sid']}")
        row = connection.execute(system_operations.insert().values(
            system_id=system_id, action=action, scope=scope, options=option if scope == "system" else None,
            hostname=hostname, instance_nr=nr, soft_timeout=soft, reason=reason, requested_by=actor, status="running",
            message="Sending request to sapstartsrv", progress="[]", maintenance_window_id=window_id,
            started_at=datetime.now(timezone.utc)).returning(system_operations)).mappings().one()
    op = view(row)
    thread = threading.Thread(target=execute, args=(op["id"], collector, system), daemon=True, name=f"sapctl-{op['id']}")
    thread.start()
    if data.get("wait"):  # tests / scripted use
        thread.join()
        return get_operation(op["id"])
    return op


def _finish(op_id: int, status: str, message: str) -> None:
    with get_db() as connection:
        connection.execute(update(system_operations).where(system_operations.c.id == op_id)
                           .values(status=status, message=message[:2000], finished_at=datetime.now(timezone.utc)))


def _progress(op_id: int, entries: list, message: str) -> None:
    with get_db() as connection:
        connection.execute(update(system_operations).where(system_operations.c.id == op_id)
                           .values(progress=json.dumps(entries[-200:]), message=message[:2000]))


def execute(op_id: int, collector: dict, system: dict) -> None:
    op = get_operation(op_id)
    try:
        client = control_client(collector)
        instances = list_instances(system["id"])
        targets = _targets(instances, op["scope"], op["options"], op["hostname"], op["instance_nr"])
        keys = {f"{t['hostname']}_{t['instance_nr']}" for t in targets}
        soft = f"<softtimeout>{op['soft_timeout']}</softtimeout>" if op["soft_timeout"] is not None else ""
        if op["scope"] == "system":
            method = {"start": "StartSystem", "stop": "StopSystem", "restart": "RestartSystem"}[op["action"]]
            params = f"<options>{OPTIONS[op['options'] or 'ALL']}</options>"
            if op["action"] == "stop" and op["soft_timeout"] is not None:
                params += soft
            client.call(method, params)
        else:
            target = targets[0]
            item = {"hostname": target["hostname"], "instanceNr": target["instance_nr"],
                    "httpPort": target["http_port"], "httpsPort": target["https_port"]}
            method = {"start": "Start", "stop": "Stop", "restart": "RestartInstance"}[op["action"]]
            client.call(method, soft if op["action"] != "start" else "", endpoint=client.instance_endpoint(item))
        _progress(op_id, [], f"{method} accepted; waiting for instances")
    except (CollectorError, OSError, ValueError) as exc:
        _finish(op_id, "failed", f"Request rejected: {exc}")
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("Control operation %s failed", op_id)
        _finish(op_id, "failed", f"Unexpected error: {exc}")
        return

    goal = {"start": "GREEN", "stop": "GRAY", "restart": "GREEN"}[op["action"]]
    started = time.monotonic()
    progress: list[dict] = []
    seen_down = op["action"] != "restart"
    status, message = "timeout", f"Not all instances reached {goal} within {config.CONTROL_TIMEOUT_SECONDS}s"
    while time.monotonic() - started < config.CONTROL_TIMEOUT_SECONDS:
        time.sleep(config.CONTROL_POLL_SECONDS)
        try:
            raw = client.call("GetSystemInstanceList")
        except CollectorError as exc:
            progress.append({"at": datetime.now(timezone.utc).isoformat(), "error": str(exc)})
            _progress(op_id, progress, f"Polling failed: {exc}")
            continue
        states = {f"{(i.get('hostname') or '').lower()}_{str(i.get('instanceNr', '')).zfill(2)}":
                  (i.get("dispstatus") or "").replace("SAPControl-", "") for i in raw}
        mine = {k: v for k, v in states.items() if k in keys}
        progress.append({"at": datetime.now(timezone.utc).isoformat(), "instances": mine})
        if not seen_down and (any(v in ("GRAY", "YELLOW") for v in mine.values())
                              or time.monotonic() - started > 120):
            seen_down = True  # a restart that was faster than our polling still counts after 2 minutes
        done = seen_down and mine and all(v == goal for v in mine.values())
        waiting = sorted(k for k, v in mine.items() if v != goal)
        _progress(op_id, progress, "All targeted instances are " + goal if done else f"Waiting for {', '.join(waiting) or '-'}")
        if done:
            status, message = "succeeded", f"{op['action'].capitalize()} completed: {len(mine)} instance(s) {goal}"
            break
        if op["action"] != "stop" and any(v == "RED" for v in mine.values()):
            status = "failed"
            message = "Instance(s) went RED: " + ", ".join(k for k, v in mine.items() if v == "RED")
            break
    _finish(op_id, status, message)
    try:  # refresh monitoring right away so dashboards reflect the new state
        runner.run(collector["id"])
    except Exception:  # noqa: BLE001
        logger.exception("Post-operation collection failed for system %s", system["id"])


def mark_interrupted() -> int:
    """Operations still 'running' after a restart of this service lost their worker thread."""
    with get_db() as connection:
        result = connection.execute(update(system_operations).where(system_operations.c.status == "running")
                                    .values(status="interrupted", finished_at=datetime.now(timezone.utc),
                                            message="Sentinel restarted while the operation was in progress; check the system"))
    return result.rowcount or 0
