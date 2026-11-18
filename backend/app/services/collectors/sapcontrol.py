"""SAP Start Service (sapstartsrv) web service client.

Calls the SOAP web methods behind ``sapcontrol``: GetSystemInstanceList, GetProcessList,
ABAPGetWPTable and ABAPReadSyslog. Endpoint: http://<host>:5<NN>13/ or https://<host>:5<NN>14/.
Protected methods (by default ABAPGetWPTable / ABAPReadSyslog depending on
service/protectedwebmethods) need the <sid>adm OS user or a user permitted by
service/admin_users; the toolkit sends HTTP Basic credentials when configured.

Responses are parsed by local element name so namespace prefixes do not matter. Methods that
are unavailable on a release (or protected without credentials) are reported per instance,
without failing the whole collection.
"""
import base64
import re
import http.client
from concurrent.futures import ThreadPoolExecutor
import socket
import ssl
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ... import config
from ..target_policy import TargetPolicyError, resolve_approved_addresses
from . import CollectorError, validate_port

ENVELOPE = ('<?xml version="1.0" encoding="utf-8"?>'
            '<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" '
            'xmlns:ns1="urn:SAPControl"><SOAP-ENV:Body>{body}</SOAP-ENV:Body></SOAP-ENV:Envelope>')


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_items(xml_text: str) -> list[dict]:
    """All <item> records in a SOAP response as {child_local_name: text}."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise CollectorError(f"Invalid SOAP response: {exc}") from exc
    for el in root.iter():
        if _local(el.tag) == "Fault":
            text = " ".join(t.strip() for t in el.itertext() if t.strip())
            raise CollectorError(f"SOAP fault: {text[:300]}")
    items = []
    for el in root.iter():
        if _local(el.tag) == "item" and len(el):
            items.append({_local(child.tag): (child.text or "").strip() for child in el})
    return items


def parse_fields(xml_text: str) -> dict[str, str]:
    """Leaf elements of a SOAP response body as {local_name: text} (methods that return one structure, e.g. EnqGetStatistic)."""
    parse_items(xml_text)  # raises on faults
    root = ET.fromstring(xml_text)
    return {_local(el.tag): (el.text or "").strip() for el in root.iter() if not len(el) and _local(el.tag) not in ("Envelope", "Body")}


def _key(name: str) -> str:
    return name.replace("-", "").replace("_", "").lower()


def _get(item: dict, *names: str, default: str = "") -> str:
    """Field lookup that ignores case, '-' and '_' (sapstartsrv uses 'lock-arg', some releases 'LockArg')."""
    keys = {_key(k): v for k, v in item.items()}
    for n in names:
        v = keys.get(_key(n))
        if v not in (None, ""):
            return v
    return default


def parse_value(xml_text: str) -> str | None:
    parse_items(xml_text)  # raises on faults
    root = ET.fromstring(xml_text)
    for el in root.iter():
        if _local(el.tag) == "value":
            return (el.text or "").strip()
    return None


@dataclass
class Endpoint:
    host: str
    port: int
    use_tls: bool
    verify_tls: bool
    username: str | None
    password: str | None


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS to a policy-validated IP while TLS SNI/hostname verification uses the SAP hostname."""

    def __init__(self, address: str, port: int, server_hostname: str, context: ssl.SSLContext, timeout: float):
        super().__init__(address, port, timeout=timeout, context=context)
        self._server_hostname = server_hostname
        self._ctx = context

    def connect(self):
        sock = socket.create_connection((self.host, self.port), self.timeout)
        self.sock = self._ctx.wrap_socket(sock, server_hostname=self._server_hostname)


def http_transport(endpoint: Endpoint, method: str, body: str) -> str:
    try:
        addresses = resolve_approved_addresses(endpoint.host, endpoint.port, check_port=False)
    except TargetPolicyError as exc:
        raise CollectorError(f"Blocked by outbound policy: {exc}") from exc
    headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": f'"urn:SAPControl/{method}"',
               "Host": f"{endpoint.host}:{endpoint.port}"}
    if endpoint.username:
        token = base64.b64encode(f"{endpoint.username}:{endpoint.password or ''}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    last_error = None
    for address in addresses:
        try:
            if endpoint.use_tls:
                ctx = ssl.create_default_context()
                if not endpoint.verify_tls:
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                conn = _PinnedHTTPSConnection(address, endpoint.port, endpoint.host, ctx, config.COLLECTOR_TIMEOUT_SECONDS)
            else:
                conn = http.client.HTTPConnection(address, endpoint.port, timeout=config.COLLECTOR_TIMEOUT_SECONDS)
            conn.request("POST", "/", body=body.encode(), headers=headers)
            response = conn.getresponse()
            text = response.read(5_000_000).decode("utf-8", "replace")
            conn.close()
            if response.status == 401:
                raise CollectorError(f"{method}: authentication required/failed (HTTP 401) - check <sid>adm credentials")
            if response.status >= 400 and "Fault" not in text:
                raise CollectorError(f"{method}: HTTP {response.status}")
            return text
        except (OSError, ssl.SSLError) as exc:
            last_error = exc
    raise CollectorError(f"{method}: cannot reach {endpoint.host}:{endpoint.port} ({last_error})")


class SapControlClient:
    def __init__(self, endpoint: Endpoint, transport=http_transport):
        self.endpoint = endpoint
        self.transport = transport

    def call(self, method: str, params: str = "", endpoint: Endpoint | None = None) -> list[dict]:
        body = ENVELOPE.format(body=f"<ns1:{method}>{params}</ns1:{method}>")
        return parse_items(self.transport(endpoint or self.endpoint, method, body))

    def call_value(self, method: str, params: str = "", endpoint: Endpoint | None = None) -> str | None:
        """Methods that return a scalar (e.g. ParameterValue -> <value>)."""
        body = ENVELOPE.format(body=f"<ns1:{method}>{params}</ns1:{method}>")
        return parse_value(self.transport(endpoint or self.endpoint, method, body))

    def instance_endpoint(self, instance: dict) -> Endpoint:
        port = int(instance.get("httpsPort") or 0) if self.endpoint.use_tls else int(instance.get("httpPort") or 0)
        if not port:
            nr = str(instance.get("instanceNr", "")).zfill(2)
            port = int(f"5{nr}14" if self.endpoint.use_tls else f"5{nr}13")
        return Endpoint(instance["hostname"], port, self.endpoint.use_tls, self.endpoint.verify_tls,
                        self.endpoint.username, self.endpoint.password)


def _int(value, default=0):
    try:
        return int(str(value).strip() or default)
    except ValueError:
        return default


def _wp(item: dict) -> dict:
    g = lambda *keys: next((item[k] for k in keys if item.get(k) not in (None, "")), "")  # noqa: E731
    return {"no": g("No", "no"), "type": g("Typ", "Type", "typ").upper(), "pid": g("Pid", "pid"),
            "status": g("Status", "status"), "reason": g("Reason", "reason"), "err": _int(g("Err", "err")),
            "time_s": _int(g("Time", "time", "ElTime")), "program": g("Program", "Report", "program"),
            "client": g("Client", "client"), "user": g("User", "user"), "action": g("Action", "action"),
            "table": g("Table", "table")}


def _syslog(item: dict, instance: str, zone) -> dict:
    from ..tabular import parse_sap_datetime
    g = lambda *keys: next((item[k] for k in keys if item.get(k) not in (None, "")), "")  # noqa: E731
    raw_time = g("Time", "time", "Timestamp", "Date")
    at = parse_sap_datetime(raw_time[:10], raw_time[11:] if len(raw_time) > 10 else "", zone) if raw_time else None
    if at is None and raw_time.isdigit() and len(raw_time) == 14:
        at = parse_sap_datetime(raw_time, "", zone)
    return {"at": at, "severity": g("Severity", "severity", "Typ", "Priority"), "msgno": g("MNo", "MsgNo", "MessageId", "Mno"),
            "text": g("Text", "text", "Message"), "user": g("User", "user"), "client": g("Client", "client"),
            "program": g("Program", "Tcode", "Report"), "instance": instance}


OWNER_TS = re.compile(r"^(\d{14})")
NUM = re.compile(r"-?\d+(?:[.,]\d+)?")


def _owner_time(owner: str, zone) -> datetime | None:
    """Enqueue owner IDs start with the lock's creation time (YYYYMMDDhhmmss, server local time), as SM12 shows it."""
    m = OWNER_TS.match(owner or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d%H%M%S").replace(tzinfo=zone).astimezone(timezone.utc)
    except ValueError:
        return None


def parse_locks(items: list[dict], zone, now: datetime) -> list[dict]:
    """EnqGetLockTable items -> SM12 rows (age from the owner ID; None when the ID carries no time)."""
    out = []
    for it in items:
        locked_at = _owner_time(_get(it, "owner"), zone)
        if locked_at and locked_at > now + timedelta(minutes=5):
            locked_at = None  # clock skew or an unknown owner format: do not invent an age
        out.append({"client": _get(it, "client")[:3] or None, "user_name": _get(it, "user", "uname")[:40] or "?",
                    "lock_mode": _get(it, "lock-mode", "mode")[:2] or None,
                    "table_name": _get(it, "lock-name", "name", "table").upper()[:64] or "?",
                    "argument": _get(it, "lock-arg", "argument") or None, "transaction": _get(it, "transaction") or None,
                    "backup": _get(it, "backup").upper() in ("TRUE", "X", "1"), "locked_at": locked_at,
                    "age_minutes": max(0, int((now - locked_at).total_seconds() // 60)) if locked_at else None})
    return out


def parse_enqueue_statistic(fields: dict) -> dict:
    def num(*names):
        v = _get(fields, *names)
        return int(v) if v.lstrip("-").isdigit() else None
    stat = {"locks_now": num("locks-now", "entries-now"), "locks_high": num("locks-high", "entries-high"),
            "locks_max": num("locks-max", "entries-max"), "owners_now": num("owner-now"), "owners_max": num("owner-max"),
            "arguments_now": num("arguments-now"), "arguments_max": num("arguments-max"),
            "enqueue_requests": num("enqueue-requests"), "enqueue_rejects": num("enqueue-rejects"),
            "enqueue_errors": num("enqueue-errors")}
    usage = [stat[a] / stat[b] * 100 for a, b in (("locks_now", "locks_max"), ("owners_now", "owners_max"),
                                                  ("arguments_now", "arguments_max")) if stat[a] is not None and stat[b]]
    stat["table_used_pct"] = round(max(usage), 2) if usage else None
    return stat


def parse_queues(items: list[dict]) -> list[dict]:
    """GetQueueStatistic: dispatcher/ICM request queues (Typ e.g. ABAP/DIA, ABAP/UPD, ABAP/BTC, ICM/Intern)."""
    out = []
    for it in items:
        def n(name):
            v = _get(it, name)
            return int(v) if v.lstrip("-").isdigit() else 0
        out.append({"type": _get(it, "Typ", "type"), "now": n("Now"), "high": n("High"), "max": n("Max")})
    return out


def parse_alert_tree(items: list[dict]) -> list[dict]:
    """GetAlertTree: the instance's CCMS monitoring tree (shared memory), flattened to paths with current colour and value."""
    nodes = []
    for it in items:
        parent = _get(it, "parent")
        nodes.append({"name": _get(it, "name"), "parent": int(parent) if parent.lstrip("-").isdigit() else -1,
                      "color": _get(it, "ActualValue").replace("SAPControl-", "").upper() or "GRAY",
                      "description": _get(it, "description"), "time": _get(it, "Time")})
    out = []
    for i, n in enumerate(nodes):
        path, j, guard = [n["name"]], n["parent"], 0
        while 0 <= j < len(nodes) and guard < 20:
            path.insert(0, nodes[j]["name"])
            j, guard = nodes[j]["parent"], guard + 1
        m = NUM.search(n["description"] or "")
        out.append({"path": "\\".join(path), "name": n["name"], "color": n["color"], "description": n["description"],
                    "value": float(m.group(0).replace(",", ".")) if m else None, "time": n["time"],
                    "leaf": not any(x["parent"] == i for x in nodes)})
    return out


def parse_alerts(items: list[dict]) -> list[dict]:
    """GetAlerts: open CCMS alerts of the instance (they stay open until completed in RZ20, so they can be old)."""
    return [{"object": _get(it, "Object"), "attribute": _get(it, "Attribute"),
             "color": _get(it, "Value").replace("SAPControl-", "").upper(), "description": _get(it, "Description"),
             "time": _get(it, "Time")} for it in items]


def _call_optional(client, method, endpoint, name, diagnostics, parse=None):
    try:
        raw = client.call(method, endpoint=endpoint) if parse is None else parse(method, endpoint)
        diagnostics.append({"instance": name, "call": method, "ok": True, "rows": len(raw) if hasattr(raw, "__len__") else None})
        return raw
    except CollectorError as exc:
        diagnostics.append({"instance": name, "call": method, "ok": False, "error": str(exc)})
        return None


def _collect_live(client: SapControlClient, entry: dict, endpoint: Endpoint, zone, diagnostics: list[dict]) -> dict:
    """Near-real-time ABAP signals read from the instance's shared memory through sapstartsrv (no RFC, any database)."""
    live: dict = {"instance": entry["name"]}
    feats = {f.strip() for f in entry["features"].upper().split("|")}
    running = entry["status"].upper().endswith(("GREEN", "YELLOW"))
    if not running:
        return live
    if "ABAP" in feats:
        queues = _call_optional(client, "GetQueueStatistic", endpoint, entry["name"], diagnostics)
        if queues is not None:
            live["queues"] = parse_queues(queues)
        tree = _call_optional(client, "GetAlertTree", endpoint, entry["name"], diagnostics)
        if tree is not None:
            live["ccms_tree"] = parse_alert_tree(tree)
        alerts = _call_optional(client, "GetAlerts", endpoint, entry["name"], diagnostics)
        if alerts is not None:
            live["ccms_alerts"] = parse_alerts(alerts)
    if feats & {"ENQUE", "ENQUEUE"}:  # the instance that holds the lock table (ASCS, or a central instance)
        now = datetime.now(timezone.utc)
        locks = _call_optional(client, "EnqGetLockTable", endpoint, entry["name"], diagnostics)
        if locks is not None:
            live["locks"] = parse_locks(locks, zone, now)
        stat = _call_optional(client, "EnqGetStatistic", endpoint, entry["name"], diagnostics,
                              parse=lambda m, ep: parse_fields(client.transport(ep, m, ENVELOPE.format(body=f"<ns1:{m}></ns1:{m}>"))))
        if stat is not None:
            live["enqueue"] = parse_enqueue_statistic(stat)
    return live


def _collect_instance(client: SapControlClient, inst: dict, zone) -> tuple[dict, dict | None, list[dict], list[dict], dict]:
    """One instance: process list, and for running ABAP instances the work process table and system log."""
    diagnostics: list[dict] = []
    wp_table, syslog_rows = None, []
    name = f"{inst.get('hostname')}_{str(inst.get('instanceNr', '')).zfill(2)}"
    entry = {"name": name, "hostname": inst.get("hostname"), "instance_nr": str(inst.get("instanceNr", "")).zfill(2),
             "features": inst.get("features", ""), "status": inst.get("dispstatus", ""), "processes": [], "error": None}
    endpoint = client.instance_endpoint(inst)
    try:
        validate_port("sapcontrol", endpoint.port)
        entry["processes"] = [{"name": p.get("name"), "description": p.get("description"), "status": p.get("dispstatus"),
                               "textstatus": p.get("textstatus"), "elapsed": p.get("elapsedtime"), "pid": p.get("pid")}
                              for p in client.call("GetProcessList", endpoint=endpoint)]
        diagnostics.append({"instance": name, "call": "GetProcessList", "ok": True})
    except CollectorError as exc:
        entry["error"] = str(exc)
        diagnostics.append({"instance": name, "call": "GetProcessList", "ok": False, "error": str(exc)})
    if "ABAP" in entry["features"].upper() and entry["status"].upper().endswith(("GREEN", "YELLOW")):
        try:
            wps = [_wp(i) for i in client.call("ABAPGetWPTable", endpoint=endpoint)]
            wp_table = {"instance": name, "workprocesses": wps}
            diagnostics.append({"instance": name, "call": "ABAPGetWPTable", "ok": True, "rows": len(wps)})
        except CollectorError as exc:
            diagnostics.append({"instance": name, "call": "ABAPGetWPTable", "ok": False, "error": str(exc)})
        try:
            rows = [_syslog(i, name, zone) for i in client.call("ABAPReadSyslog", endpoint=endpoint)]
            syslog_rows = [r for r in rows if r["at"]]
            diagnostics.append({"instance": name, "call": "ABAPReadSyslog", "ok": True, "rows": len(rows)})
        except CollectorError as exc:
            diagnostics.append({"instance": name, "call": "ABAPReadSyslog", "ok": False, "error": str(exc)})
    live = _collect_live(client, entry, endpoint, zone, diagnostics) if not entry["error"] else {"instance": name}
    return entry, wp_table, syslog_rows, diagnostics, live


def collect(client: SapControlClient, zone) -> dict:
    """Return availability, work process and syslog inputs plus per-call diagnostics.

    Instances are queried in parallel (SAPCONTROL_INSTANCE_PARALLEL), so a system with many application servers, or one
    unreachable instance, costs about one call's latency instead of the sum of all of them. Output order is stable."""
    raw_instances = client.call("GetSystemInstanceList")
    if not raw_instances:
        raise CollectorError("GetSystemInstanceList returned no instances")
    workers = max(1, min(len(raw_instances), config.SAPCONTROL_INSTANCE_PARALLEL))
    if workers == 1:
        results = [_collect_instance(client, inst, zone) for inst in raw_instances]
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sapcontrol") as pool:
            results = list(pool.map(lambda inst: _collect_instance(client, inst, zone), raw_instances))
    instances = [r[0] for r in results]
    wp_tables = [r[1] for r in results if r[1] is not None]
    syslog_rows = [row for r in results for row in r[2]]
    diagnostics = [d for r in results for d in r[3]]
    live = [r[4] for r in results if len(r[4]) > 1]
    return {"instances": instances, "workprocesses": wp_tables, "syslog": syslog_rows, "live": live,
            "syslog_supported": any(d["call"] == "ABAPReadSyslog" and d["ok"] for d in diagnostics),
            "diagnostics": diagnostics, "collected_at": datetime.now(timezone.utc)}
