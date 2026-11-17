"""Controlled start / stop / restart procedures for SAP systems and their HANA database.

The procedure follows the SAP standard sequence (instance start priorities, SAP Learning
"Starting and Stopping AS ABAP", KBA 3099140):

    Start:  HANA database -> ASCS/SCS -> ERS -> gateway -> PAS -> AAS / Java -> Web Dispatcher
    Stop:   Web Dispatcher -> AAS / Java -> PAS -> gateway -> ASCS/SCS -> ERS -> HANA database
    Restart: the full stop sequence, then the full start sequence

Scopes
- ``system``   SAP instances and the HANA database (needs a hana_sapcontrol collector for the DB)
- ``app``      SAP instances only; the database is left as it is
- ``db``       HANA database only; stopping it is blocked while SAP application servers run
- ``instance`` one instance (an application instance cannot start while the database is down)

Every procedure is a list of steps:
- **checks** (automatic, read-only): current state, pending update requests (VBHDR), running
  background jobs (TBTCO), active dialog work (SM50), database state. A failed blocking check
  stops the procedure unless the admin explicitly forces it.
- **manual confirmations** before the stop (users informed/locked, jobs suspended with BTCTRNS1,
  interfaces stopped, backup available) are recorded with the request.
- **actions**, one per instance group: sapcontrol ``Start``/``Stop`` on each instance's own
  sapstartsrv (for HANA: the HDB instance, equivalent to ``HDB start``/``HDB stop``), then polling
  ``GetSystemInstanceList`` until every instance of the group is GREEN/GRAY. A group must finish
  before the next one begins. RED, a timeout or an abort halts the procedure.
- **post steps**: verification, a HANA SQL connectivity check after the database start, and
  manual follow-ups (release jobs with BTCTRNS2, unlock users, resume interfaces, check SM21/ST22)
  that can be ticked off afterwards.
"""
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from .. import config
from ..db import collectors, get_db, system_operations
from . import incidents, systems
from .collectors import CollectorError, runner
from .discovery import list_instances

logger = logging.getLogger(__name__)

ACTIONS = ("start", "stop", "restart")
SCOPES = ("system", "app", "db", "instance")
SCOPE_LABELS = {"system": "SAP system and HANA database", "app": "SAP application only", "db": "HANA database only",
                "instance": "single instance"}
APP_ROLES = {"ASCS", "SCS", "ERS", "PAS", "AAS", "JAVA", "WEBDISP", "GATEWAY"}
DIALOG_ROLES = {"PAS", "AAS", "JAVA"}

STOP_SEQUENCE = [
    ("webdisp", "Stop Web Dispatcher", {"WEBDISP"}),
    ("aas", "Stop additional application servers (AAS / Java)", {"AAS", "JAVA"}),
    ("pas", "Stop primary application server (PAS)", {"PAS"}),
    ("gateway", "Stop standalone gateway", {"GATEWAY"}),
    ("cs", "Stop central services (ASCS / SCS)", {"ASCS", "SCS"}),
    ("ers", "Stop enqueue replication server (ERS)", {"ERS"}),
    ("db", "Stop HANA database", {"HANA"}),
]
START_SEQUENCE = [
    ("db", "Start HANA database", {"HANA"}),
    ("cs", "Start central services (ASCS / SCS)", {"ASCS", "SCS"}),
    ("ers", "Start enqueue replication server (ERS)", {"ERS"}),
    ("gateway", "Start standalone gateway", {"GATEWAY"}),
    ("pas", "Start primary application server (PAS)", {"PAS"}),
    ("aas", "Start additional application servers (AAS / Java)", {"AAS", "JAVA"}),
    ("webdisp", "Start Web Dispatcher", {"WEBDISP"}),
]
PRE_STOP_MANUAL = [
    ("users_locked", "Users informed and locked (SM02 message, SU10 / EWZ5)"),
    ("jobs_suspended", "Background jobs suspended (report BTCTRNS1)"),
    ("interfaces_stopped", "Inbound/outbound queues and interfaces stopped (SMQR / SMQS, PI channels)"),
    ("backup_ok", "Recent database backup available"),
]
POST_START_MANUAL = [
    ("jobs_released", "Release background jobs (report BTCTRNS2)"),
    ("users_unlocked", "Unlock users (SU10 / EWZ5)"),
    ("interfaces_resumed", "Resume queues and interfaces (SMQR / SMQS)"),
    ("logs_checked", "Check system log and dumps (SM21, ST22) and do a test logon"),
]


class ControlError(ValueError):
    pass


class ControlConflict(ControlError):
    pass


def _now():
    return datetime.now(timezone.utc)


def _iso(v):
    if v is None:
        return None
    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).isoformat()


def view(row) -> dict:
    item = dict(row)
    item["progress"] = json.loads(item["progress"]) if item.get("progress") else []
    item["steps"] = json.loads(item["steps"]) if item.get("steps") else []
    item["checklist"] = json.loads(item["checklist"]) if item.get("checklist") else {}
    item["force"] = bool(item.get("force"))
    item["abort_requested"] = bool(item.get("abort_requested"))
    item["scope_label"] = SCOPE_LABELS.get(item["scope"], item["scope"])
    for k in ("started_at", "finished_at"):
        item[k] = _iso(item[k])
    return item


# ------------------------------------------------------------------------------- policy & endpoints
def control_allowed(system: dict) -> tuple[bool, str | None]:
    if not config.CONTROL_ENABLED:
        return False, "Start/stop is disabled on this server (CONTROL_ENABLED=false)"
    flag = system.get("control_enabled")
    if flag is None:
        if system.get("environment") == "PRD":
            return False, "Start/stop is off for production systems until an admin enables it in the system settings"
        return True, None
    return (True, None) if flag else (False, "Start/stop is disabled for this system")


def _collector(system_id: int, kind: str) -> dict | None:
    with get_db() as connection:
        row = connection.execute(select(collectors).where(collectors.c.system_id == system_id, collectors.c.type == kind)
                                 .order_by(collectors.c.id)).mappings().first()
    return dict(row) if row else None


class Endpoints:
    """sapcontrol clients for the application (ASCS/PAS/AAS ...) and the HANA instance, plus HANA SQL."""

    def __init__(self, system: dict):
        self.system = system
        self.app_row = _collector(system["id"], "sapcontrol")
        self.db_row = _collector(system["id"], "hana_sapcontrol")
        self.sql_row = _collector(system["id"], "hana")
        self.app = runner.sapcontrol_client(self.app_row, system) if self.app_row else None
        self.db = runner.sapcontrol_client(self.db_row, system) if self.db_row else None

    def states(self, which: str) -> dict[str, str]:
        client = self.db if which == "db" else self.app
        if client is None:
            return {}
        return {f"{(i.get('hostname') or '').lower()}_{str(i.get('instanceNr', '')).zfill(2)}":
                (i.get("dispstatus") or "").replace("SAPControl-", "") for i in client.call("GetSystemInstanceList")}

    def sql(self, name: str, sql: str) -> list[dict] | None:
        if not self.sql_row:
            return None
        schema = self.sql_row.get("abap_schema")
        if "{schema}" in sql:
            if not schema:
                return None
            sql = sql.replace("{schema}", f'"{schema}"')
        client = runner.hana_client(self.sql_row, self.system)
        try:
            return client.query(name, sql)
        finally:
            client.close()


def capabilities(system_id: int) -> dict:
    system = systems.get_system(system_id)
    allowed, why = control_allowed(system)
    app_row, db_row = _collector(system_id, "sapcontrol"), _collector(system_id, "hana_sapcontrol")
    app_ok = bool(app_row and app_row["username"])
    db_ok = bool(db_row and db_row["username"])
    reason = why
    if allowed and not app_ok and not db_ok:
        reason = "No sapcontrol collector with OS credentials: run discovery or add one under Data collection"
    scopes = {"system": app_ok and (db_ok or system["database"] != "HANA"), "app": app_ok,
              "db": db_ok, "instance": app_ok or db_ok}
    notes = []
    if system["database"] == "HANA" and not db_ok:
        notes.append("Add the HANA instance (collector type 'HANA instance (sapcontrol)' with <hanasid>adm) "
                     "to include the database in start/stop procedures.")
    if system["database"] != "HANA":
        notes.append(f"The {system['database']} database is not controlled by Sentinel; start it before the SAP system "
                     "and stop it afterwards with SAP Host Agent or the database tools.")
    return {"allowed": allowed and (app_ok or db_ok), "reason": reason, "scopes": scopes, "notes": notes,
            "running": running_operation(system_id), "timeout_seconds": config.CONTROL_TIMEOUT_SECONDS,
            "hana_start_timeout_seconds": config.HANA_START_TIMEOUT_SECONDS,
            "start_sequence": [label for _, label, _ in START_SEQUENCE],
            "stop_sequence": [label for _, label, _ in STOP_SEQUENCE]}


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


# ------------------------------------------------------------------------------- plan
def _key(inst: dict) -> str:
    return f"{inst['hostname']}_{inst['instance_nr']}"


def _step(key: str, kind: str, title: str, **extra) -> dict:
    return {"key": key, "kind": kind, "title": title, "status": "pending", "detail": None, **extra}


def build_plan(system: dict, action: str, scope: str, hostname: str | None = None, nr: str | None = None,
               checklist: dict | None = None) -> list[dict]:
    """Ordered steps for the procedure (checks are evaluated later)."""
    checklist = checklist or {}
    if action not in ACTIONS:
        raise ControlError(f"action must be one of {', '.join(ACTIONS)}")
    if scope not in SCOPES:
        raise ControlError(f"scope must be one of {', '.join(SCOPES)}")
    instances = list_instances(system["id"])
    caps = capabilities(system["id"])
    if not caps["scopes"].get(scope):
        raise ControlError(f"'{SCOPE_LABELS[scope]}' is not available for this system. " + " ".join(caps["notes"]))

    if scope == "instance":
        target = next((i for i in instances if i["hostname"] == hostname and i["instance_nr"] == nr), None)
        if target is None:
            raise ControlError("Unknown instance; refresh the instance list first")
        if (target["role"] == "HANA" and not caps["scopes"]["db"]) or (target["role"] != "HANA" and not caps["scopes"]["app"]):
            raise ControlError("No sapcontrol credentials for this instance")
        if target["role"] not in APP_ROLES | {"HANA"}:
            raise ControlError(f"Instances with role {target['role']} are not controlled by Sentinel")
        selected = [target]
    else:
        roles = {"system": APP_ROLES | {"HANA"}, "app": APP_ROLES, "db": {"HANA"}}[scope]
        selected = [i for i in instances if i["role"] in roles]
        if not caps["scopes"]["db"]:
            selected = [i for i in selected if i["role"] != "HANA"]
        if not selected:
            raise ControlError("No instances to control; refresh the instance list first")
    roles_in = {i["role"] for i in selected}
    app_in = bool(roles_in & APP_ROLES)
    db_in = "HANA" in roles_in
    stopping = action in ("stop", "restart")
    starting = action in ("start", "restart")

    steps = [_step("check_state", "check", "Check current instance states")]
    if stopping and (roles_in & DIALOG_ROLES):
        steps += [_step("check_updates", "check", "No pending update requests (SM13 / VBHDR)", blocking=True),
                  _step("check_jobs", "check", "Running background jobs (SM37)"),
                  _step("check_users", "check", "Active dialog work (SM50)")]
    if stopping and db_in and not app_in:
        steps.append(_step("check_app_down", "check", "SAP application servers are stopped", blocking=True))
    if starting and (roles_in & DIALOG_ROLES) and not db_in:
        steps.append(_step("check_db_up", "check", "Database is running", blocking=True))
    if stopping and scope != "instance":
        for key, title in PRE_STOP_MANUAL:
            if key != "backup_ok" and not app_in:
                continue
            steps.append(_step(f"pre_{key}", "manual", title,
                               status="confirmed" if checklist.get(key) else "not_confirmed"))

    def groups(sequence, verb):
        for key, title, roles in sequence:
            members = [i for i in selected if i["role"] in roles]
            if not members:
                continue
            if scope == "instance":
                title = f"{verb.capitalize()} {members[0]['instance_name'] or members[0]['role']} on {members[0]['hostname']}"
            steps.append(_step(f"{verb}_{key}", "action", title, verb=verb, group=key,
                               targets=[_key(m) for m in members], roles=sorted({m["role"] for m in members}), instances={}))
            if verb == "start" and key == "db" and _collector(system["id"], "hana"):
                steps.append(_step("check_sql", "check", "HANA tenant accepts SQL connections", blocking=True))
    if stopping:
        groups(STOP_SEQUENCE, "stop")
    if starting:
        groups(START_SEQUENCE, "start")
    steps.append(_step("verify", "check", "Verify final state", blocking=True))
    if starting and app_in and scope != "instance":
        steps += [_step(f"post_{k}", "manual", t, status="todo") for k, t in POST_START_MANUAL]
    return steps


# ------------------------------------------------------------------------------- checks
def _run_check(step: dict, ctx: dict, action: str) -> None:
    ep: Endpoints = ctx["ep"]
    key = step["key"]
    try:
        if key == "check_state":
            states = {**(ep.states("app") if ep.app else {}), **(ep.states("db") if ep.db else {})}
            step["instances"] = states
            counts = {s: sum(1 for v in states.values() if v == s) for s in ("GREEN", "YELLOW", "RED", "GRAY")}
            step["status"] = "ok"
            step["detail"] = ", ".join(f"{n} {s}" for s, n in counts.items() if n) or "no instances reported"
        elif key == "check_updates":
            rows = ep.sql("pending_updates", "SELECT COUNT(*) AS N FROM {schema}.VBHDR")
            if rows is None:
                step["status"], step["detail"] = "warning", "Not readable (no HANA SQL collector with ABAP schema): check SM13 manually"
            else:
                n = int(rows[0]["N"] or 0)
                step["status"] = "ok" if n == 0 else "failed"
                step["detail"] = ("No pending update requests" if n == 0 else
                                  f"{n} update request(s) pending in SM13: let them finish or process them first")
        elif key == "check_jobs":
            rows = ep.sql("running_jobs", "SELECT COUNT(*) AS N FROM {schema}.TBTCO WHERE STATUS = 'R'")
            if rows is None:
                step["status"], step["detail"] = "warning", "Not readable: check SM37 manually"
            else:
                n = int(rows[0]["N"] or 0)
                step["status"] = "ok" if n == 0 else "warning"
                step["detail"] = "No running jobs" if n == 0 else f"{n} job(s) running; the stop will cancel them"
        elif key == "check_users":
            busy = 0
            for inst in ep.app.call("GetSystemInstanceList") if ep.app else []:
                if "ABAP" not in (inst.get("features") or "") or not (inst.get("dispstatus") or "").endswith("GREEN"):
                    continue
                try:
                    wps = ep.app.call("ABAPGetWPTable", endpoint=ep.app.instance_endpoint(inst))
                except CollectorError:
                    continue
                busy += sum(1 for w in wps if (w.get("Typ") or "").upper() == "DIA"
                            and (w.get("Status") or "").lower().startswith("run") and (w.get("User") or "").strip())
            step["status"] = "ok" if busy == 0 else "warning"
            step["detail"] = "No active dialog work" if busy == 0 else f"{busy} dialog work process(es) busy with user requests"
        elif key == "check_app_down":
            apps = {_key(i) for i in list_instances(ep.system["id"]) if i["role"] in DIALOG_ROLES}
            running = sorted(k for k, v in (ep.states("app") if ep.app else {}).items()
                             if k in apps and v in ("GREEN", "YELLOW"))
            step["status"] = "ok" if not running else "failed"
            step["detail"] = ("Application servers are down" if not running else
                              "Still running: " + ", ".join(running) + " — stop the SAP application first")
        elif key == "check_db_up":
            if ep.db is None:
                step["status"], step["detail"] = "warning", "Database state unknown (no HANA instance collector): make sure it is running"
            else:
                states = ep.states("db")
                up = bool(states) and all(v == "GREEN" for v in states.values())
                step["status"] = "ok" if up else "failed"
                step["detail"] = "HANA is running" if up else "HANA is not running: start the database first"
        elif key == "check_sql":
            deadline = time.monotonic() + config.HANA_SQL_WAIT_SECONDS
            while True:
                try:
                    ep.sql("ping", "SELECT 1 AS OK FROM DUMMY")
                    step["status"], step["detail"] = "ok", "SQL connection to the tenant succeeded"
                    break
                except Exception as exc:  # noqa: BLE001 - hdbcli / connection errors
                    if time.monotonic() > deadline or ctx["aborted"]():
                        step["status"], step["detail"] = "failed", f"Tenant not reachable: {exc}"
                        break
                time.sleep(config.CONTROL_POLL_SECONDS)
        elif key == "verify":
            goal = "GRAY" if action == "stop" else "GREEN"
            states = {**(ep.states("app") if ep.app else {}), **(ep.states("db") if ep.db else {})}
            mine = {k: states.get(k, "MISSING") for k in ctx["targets"]}
            step["instances"] = mine
            bad = sorted(k for k, v in mine.items() if v != goal)
            step["status"] = "ok" if not bad else "failed"
            step["detail"] = f"All {len(mine)} instance(s) {goal}" if not bad else f"Not {goal}: {', '.join(bad)}"
    except CollectorError as exc:
        step["status"] = "failed" if step.get("blocking") or key == "check_state" else "warning"
        step["detail"] = str(exc)


def evaluate_plan(system_id: int, action: str, scope: str, hostname: str | None = None, nr: str | None = None) -> dict:
    """Plan with the pre-checks evaluated (read-only) so the admin sees blockers before confirming."""
    system = systems.get_system(system_id)
    hostname = (hostname or "").lower() or None
    nr = nr.zfill(2) if nr else None
    steps = build_plan(system, action, scope, hostname, nr)
    ctx = {"ep": Endpoints(system), "aborted": lambda: False}
    for step in steps:
        if step["kind"] == "check" and step["key"] not in ("verify", "check_sql"):
            _run_check(step, ctx, action)
    blockers = [s["title"] + (f": {s['detail']}" if s.get("detail") else "") for s in steps
                if s["kind"] == "check" and s["status"] == "failed"]
    manual = [{"key": s["key"][4:], "title": s["title"]} for s in steps if s["key"].startswith("pre_")]
    return {"action": action, "scope": scope, "scope_label": SCOPE_LABELS[scope], "steps": steps,
            "blocked": bool(blockers), "blockers": blockers, "manual": manual}


# ------------------------------------------------------------------------------- request / execute
def request(system_id: int, data: dict, actor: str) -> dict:
    system = systems.get_system(system_id)
    allowed, why = control_allowed(system)
    if not allowed:
        raise ControlError(why)
    action = (data.get("action") or "").lower()
    scope = (data.get("scope") or "system").lower()
    if (data.get("confirm_sid") or "").strip().upper() != system["sid"]:
        raise ControlError(f"Type the SID ({system['sid']}) to confirm")
    reason = (data.get("reason") or "").strip()
    if len(reason) < 5:
        raise ControlError("Give a reason (at least 5 characters); it is recorded in the audit trail")
    soft = data.get("soft_timeout")
    soft = int(soft) if soft not in (None, "") else None
    if soft is not None and not 0 <= soft <= 3600:
        raise ControlError("soft_timeout must be between 0 and 3600 seconds")
    hostname = (data.get("hostname") or "").lower() or None
    nr = str(data["instance_nr"]).zfill(2) if data.get("instance_nr") not in (None, "") else None
    checklist = {k: bool(v) for k, v in (data.get("checklist") or {}).items() if k in dict(PRE_STOP_MANUAL)}
    steps = build_plan(system, action, scope, hostname, nr, checklist)
    minutes = int(data.get("maintenance_minutes") or 0)
    if minutes and not 5 <= minutes <= 24 * 60:
        raise ControlError("maintenance_minutes must be between 5 and 1440")
    if running_operation(system_id):
        raise ControlConflict(f"Operation {running_operation(system_id)['id']} is still running for {system['sid']}")
    window_id = None
    if minutes:
        now = _now()
        window_id = incidents.create_maintenance({"scope_type": "system", "scope_value": str(system_id), "starts_at": now,
                                                  "ends_at": now + timedelta(minutes=minutes),
                                                  "reason": f"{action} {system['sid']}: {reason}"[:500]}, actor)["id"]
    with get_db() as connection:
        row = connection.execute(system_operations.insert().values(
            system_id=system_id, action=action, scope=scope, options=None, hostname=hostname, instance_nr=nr,
            soft_timeout=soft, reason=reason, requested_by=actor, status="running", message="Starting procedure",
            progress="[]", steps=json.dumps(steps), checklist=json.dumps(checklist), force=bool(data.get("force")),
            abort_requested=False, maintenance_window_id=window_id, started_at=_now()).returning(system_operations)).mappings().one()
    op = view(row)
    thread = threading.Thread(target=execute, args=(op["id"],), daemon=True, name=f"sapctl-{op['id']}")
    thread.start()
    if data.get("wait"):  # tests / scripted use
        thread.join()
        return get_operation(op["id"])
    return op


def _save(op_id: int, steps: list, message: str, **extra) -> None:
    with get_db() as connection:
        connection.execute(update(system_operations).where(system_operations.c.id == op_id)
                           .values(steps=json.dumps(steps, default=str), message=message[:2000], **extra))


def _aborted(op_id: int) -> bool:
    with get_db() as connection:
        return bool(connection.execute(select(system_operations.c.abort_requested)
                                       .where(system_operations.c.id == op_id)).scalar())


def _safe(fn, arg):
    try:
        fn(arg)
        return None
    except (CollectorError, OSError, ValueError) as exc:
        return str(exc)


def _act(step: dict, ep: Endpoints, op: dict, steps: list) -> None:
    """Start or stop every instance of one group in parallel, then wait for the goal state."""
    op_id = op["id"]
    instances = {_key(i): i for i in list_instances(op["system_id"])}
    goal = "GREEN" if step["verb"] == "start" else "GRAY"
    db_group = step["roles"] == ["HANA"]
    client = ep.db if db_group else ep.app
    if client is None:
        step["status"], step["detail"] = "failed", "No sapcontrol endpoint for this group"
        return
    soft = (f"<softtimeout>{op['soft_timeout']}</softtimeout>"
            if op["soft_timeout"] is not None and step["verb"] == "stop" and not db_group else "")

    def call(key):
        inst = instances[key]
        endpoint = client.instance_endpoint({"hostname": inst["hostname"], "instanceNr": inst["instance_nr"],
                                             "httpPort": inst["http_port"], "httpsPort": inst["https_port"]})
        client.call("Start" if step["verb"] == "start" else "Stop", soft, endpoint=endpoint)

    with ThreadPoolExecutor(max_workers=max(1, min(8, len(step["targets"])))) as pool:
        results = list(pool.map(lambda k: _safe(call, k), step["targets"]))
    errors = [f"{k}: {e}" for k, e in zip(step["targets"], results) if e]
    if errors:
        step["status"], step["detail"] = "failed", "Request rejected — " + "; ".join(errors)
        return
    step["detail"] = f"{step['verb'].capitalize()} sent; waiting for {goal}"
    _save(op_id, steps, f"{step['title']}: {step['detail']}")
    timeout = config.HANA_START_TIMEOUT_SECONDS if db_group and step["verb"] == "start" else config.CONTROL_TIMEOUT_SECONDS
    started = time.monotonic()
    while True:
        time.sleep(config.CONTROL_POLL_SECONDS)
        if _aborted(op_id):
            step["status"], step["detail"] = "aborted", "Aborted while waiting"
            return
        elapsed = time.monotonic() - started
        try:
            states = ep.states("db" if db_group else "app")
        except CollectorError as exc:
            step["detail"] = f"Polling failed: {exc}"
            _save(op_id, steps, step["detail"])
            if elapsed > timeout:
                step["status"] = "timeout"
                return
            continue
        mine = {k: states.get(k, "MISSING") for k in step["targets"]}
        step["instances"] = mine
        waiting = sorted(k for k, v in mine.items() if v != goal)
        if not waiting:
            step["status"], step["detail"] = "ok", f"{len(mine)} instance(s) {goal} after {int(elapsed)}s"
            return
        if goal == "GREEN" and any(v == "RED" for v in mine.values()):
            step["status"] = "failed"
            step["detail"] = ("Instance(s) RED: " + ", ".join(k for k, v in mine.items() if v == "RED")
                              + " — check the work directory traces (dev_w*, dev_disp, dev_ms)")
            return
        if elapsed > timeout:
            step["status"], step["detail"] = "timeout", f"Still waiting for {', '.join(waiting)} after {timeout}s"
            return
        step["detail"] = f"Waiting for {', '.join(waiting)}"
        _save(op_id, steps, f"{step['title']}: {step['detail']}")


def execute(op_id: int) -> None:
    op = get_operation(op_id)
    steps = op["steps"]
    try:
        system = systems.get_system(op["system_id"])
        ep = Endpoints(system)
    except Exception as exc:  # noqa: BLE001
        _finish(op_id, steps, "failed", f"Cannot prepare the procedure: {exc}")
        return
    ctx = {"ep": ep, "aborted": lambda: _aborted(op_id),
           "targets": sorted({t for s in steps if s["kind"] == "action" for t in s["targets"]})}
    final, message = "succeeded", ""
    for step in steps:
        if step["kind"] == "manual":
            continue
        if _aborted(op_id):
            final, message = "aborted", f"Aborted before '{step['title']}'"
            break
        step["status"], step["started_at"] = "running", _now().isoformat()
        _save(op_id, steps, step["title"])
        try:
            if step["kind"] == "check":
                _run_check(step, ctx, op["action"])
            else:
                _act(step, ep, op, steps)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Operation %s step %s failed", op_id, step["key"])
            step["status"], step["detail"] = "failed", f"Unexpected error: {exc}"
        step["finished_at"] = _now().isoformat()
        _save(op_id, steps, f"{step['title']}: {step.get('detail') or step['status']}")
        if step["status"] == "aborted":
            final, message = "aborted", f"Aborted during '{step['title']}'"
            break
        if step["status"] in ("failed", "timeout"):
            if step["kind"] == "check" and step.get("blocking") and op["force"] and step["key"] not in ("verify", "check_state"):
                step["status"] = "overridden"
                continue
            final = "timeout" if step["status"] == "timeout" else "failed"
            message = f"Halted at '{step['title']}': {step.get('detail')}"
            break
    if final != "succeeded":
        for step in steps:
            if step["status"] == "pending":
                step["status"] = "skipped"
        if any(s["kind"] == "action" and s["status"] not in ("pending", "skipped") for s in steps):
            message += " — the system may be partially started/stopped; check the instance states"
    else:
        warnings = sum(1 for s in steps if s["status"] in ("warning", "overridden", "not_confirmed"))
        message = f"{op['action'].capitalize()} procedure completed" + (f" with {warnings} warning(s)" if warnings else "")
        todo = sum(1 for s in steps if s["status"] == "todo")
        if todo:
            message += f"; {todo} follow-up step(s) to confirm"
    _finish(op_id, steps, final, message)
    for row in (ep.app_row, ep.db_row):  # refresh monitoring right away
        if row:
            try:
                runner.run(row["id"])
            except Exception:  # noqa: BLE001
                logger.exception("Post-operation collection failed for collector %s", row["id"])


def _finish(op_id: int, steps: list, status: str, message: str) -> None:
    last: dict = {}
    for s in steps:
        last.update(s.get("instances") or {})
    _save(op_id, steps, message, status=status, finished_at=_now(),
          progress=json.dumps([{"at": _now().isoformat(), "instances": last}]))


def abort(op_id: int, actor: str) -> dict:
    op = get_operation(op_id)
    if op["status"] != "running":
        raise ControlError("The operation is not running")
    with get_db() as connection:
        connection.execute(update(system_operations).where(system_operations.c.id == op_id)
                           .values(abort_requested=True, message=f"Abort requested by {actor}"))
    return get_operation(op_id)


def confirm_step(op_id: int, step_key: str, actor: str, done: bool = True) -> dict:
    op = get_operation(op_id)
    steps = op["steps"]
    step = next((s for s in steps if s["key"] == step_key and s["kind"] == "manual"), None)
    if step is None or not step_key.startswith("post_"):
        raise LookupError("Follow-up step not found")
    if op["status"] == "running":
        raise ControlError("Wait until the procedure has finished")
    step["status"] = "confirmed" if done else "todo"
    step["detail"] = f"Confirmed by {actor} at {_now().strftime('%Y-%m-%d %H:%M UTC')}" if done else None
    with get_db() as connection:
        connection.execute(update(system_operations).where(system_operations.c.id == op_id).values(steps=json.dumps(steps)))
    return get_operation(op_id)


def mark_interrupted() -> int:
    """Operations still 'running' after a restart of this service lost their worker thread."""
    with get_db() as connection:
        result = connection.execute(update(system_operations).where(system_operations.c.status == "running")
                                    .values(status="interrupted", finished_at=_now(),
                                            message="Sentinel restarted while the procedure was running; check the system"))
    return result.rowcount or 0
