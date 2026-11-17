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
import http.client
import socket
import ssl
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone

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


def collect(client: SapControlClient, zone) -> dict:
    """Return availability, work process and syslog inputs plus per-call diagnostics."""
    diagnostics: list[dict] = []
    raw_instances = client.call("GetSystemInstanceList")
    if not raw_instances:
        raise CollectorError("GetSystemInstanceList returned no instances")
    instances, wp_tables, syslog_rows = [], [], []
    for inst in raw_instances:
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
                wp_tables.append({"instance": name, "workprocesses": wps})
                diagnostics.append({"instance": name, "call": "ABAPGetWPTable", "ok": True, "rows": len(wps)})
            except CollectorError as exc:
                diagnostics.append({"instance": name, "call": "ABAPGetWPTable", "ok": False, "error": str(exc)})
            try:
                rows = [_syslog(i, name, zone) for i in client.call("ABAPReadSyslog", endpoint=endpoint)]
                syslog_rows.extend(r for r in rows if r["at"])
                diagnostics.append({"instance": name, "call": "ABAPReadSyslog", "ok": True, "rows": len(rows)})
            except CollectorError as exc:
                diagnostics.append({"instance": name, "call": "ABAPReadSyslog", "ok": False, "error": str(exc)})
        instances.append(entry)
    return {"instances": instances, "workprocesses": wp_tables, "syslog": syslog_rows,
            "syslog_supported": any(d["call"] == "ABAPReadSyslog" and d["ok"] for d in diagnostics),
            "diagnostics": diagnostics, "collected_at": datetime.now(timezone.utc)}
