"""RFC collector: standard, remote-enabled function modules on the ABAP stack.

Uses the PyRFC client API (``pyrfc.Connection(...).call(name, **params)``). Install either SAP's archived ``pyrfc``
(needs the SAP NW RFC SDK, downloaded by the customer from SAP) or a drop-in replacement that provides the same module.
Nothing here reads tables generically (no RFC_READ_TABLE): only these released or ST-PI function modules are called.

| Function module            | Purpose                                        | Authorisation (S_RFC, plus)          |
|----------------------------|------------------------------------------------|---------------------------------------|
| RFC_SYSTEM_INFO            | SID, release, database, host (connection test) | -                                     |
| TH_SERVER_LIST             | Application servers registered at the message server | -                               |
| /SDF/GET_DUMP_LOG (ST-PI)  | ST22 dump list for a time window               | S_RFC on /SDF/*, /SDF/E2E 03          |
| BAPI_XMI_LOGON / LOGOFF    | Open/close an XBP session                      | S_XMI_PROD                            |
| BAPI_XBP_JOB_SELECT        | SM37 job list through the external job interface | S_XMI_PROD, S_BTCH_ADM or S_BTCH_JOB |
"""
import re
from datetime import datetime, timedelta, timezone

from ... import config
from ..target_policy import TargetPolicyError, resolve_approved_addresses
from . import CollectorError, simulator

CLIENT_RE = re.compile(r"^\d{3}$")
XBP_COMPANY, XBP_PRODUCT = "SENTINEL", "SAP Basis Sentinel"


class RfcClient:
    """One RFC connection (application server logon: ashost + sysnr)."""

    def __init__(self, host: str, port: int, client: str, user: str, password: str):
        if not CLIENT_RE.match(client or ""):
            raise CollectorError("RFC needs the SAP client (three digits, e.g. 100)")
        sysnr = f"{int(port) % 100:02d}"
        try:
            from pyrfc import Connection  # type: ignore[import-not-found]
        except ImportError as exc:
            raise CollectorError("No RFC library in this deployment: build the image with RFC_CLIENT=true and the SAP NW RFC "
                                 "SDK, or install a PyRFC-compatible package (see docs/abap-data-sources.md)") from exc
        try:
            address = resolve_approved_addresses(host, port, check_port=False)[0]
        except TargetPolicyError as exc:
            raise CollectorError(f"Blocked by outbound policy: {exc}") from exc
        try:
            self.conn = Connection(ashost=address, sysnr=sysnr, client=client, user=user, passwd=password,
                                   lang="EN", config={"timeout": int(config.COLLECTOR_TIMEOUT_SECONDS)})
        except Exception as exc:  # pyrfc raises LogonError / CommunicationError
            raise CollectorError(f"RFC logon failed: {_short(exc)}") from exc

    def call(self, name: str, **params) -> dict:
        try:
            return self.conn.call(name, **params)
        except Exception as exc:  # ABAPApplicationError, ABAPRuntimeError, CommunicationError
            raise CollectorError(f"{name}: {_short(exc)}") from exc

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass


def _short(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    return text[:300]


def make_client(row: dict, password: str | None, seed: str):
    if simulator.is_sim(row["host"]):
        return simulator.SimulatedRfc(seed)
    return RfcClient(row["host"], int(row["port"]), row.get("sap_client") or "", row["username"] or "", password or "")


def _d(value: datetime, zone) -> tuple[str, str]:
    local = value.astimezone(zone)
    return local.strftime("%Y%m%d"), local.strftime("%H%M%S")


def _rows(result: dict, *names: str) -> list[dict]:
    for n in names:
        if isinstance(result.get(n), list):
            return result[n]
    return []


def _bapi_ok(result: dict) -> str | None:
    """BAPIRET2 / BAPIRETURN: return the message when TYPE is E or A."""
    ret = result.get("RETURN") or {}
    if isinstance(ret, list):
        ret = next((r for r in ret if r.get("TYPE") in ("E", "A")), {})
    return ret.get("MESSAGE") if ret.get("TYPE") in ("E", "A") else None


# Column detection for /SDF/GET_DUMP_LOG: the result table is generic and comes with a description structure
# (ES_E2E_LOG_STRUCT_DESC) that names each column. Semantic names are tried first, then the descriptions.
DUMP_FIELDS = {
    "date": (("DATUM", "DATE"), ("date",)),
    "time": (("UZEIT", "TIME"), ("time",)),
    "timestamp": (("TIMESTAMP", "TIME_STAMP"), ("timestamp", "time stamp")),
    "error": (("ERRID", "RUNTIME_ERROR", "ERROR_ID"), ("runtime error", "error id", "error")),
    "user": (("UNAME", "USER", "USERNAME"), ("user",)),
    "host": (("AHOST", "HOST", "SERVER"), ("server", "host", "instance")),
    "client": (("MANDT", "CLIENT"), ("client",)),
    "program": (("PROGRAM", "PROG", "MAINPROG"), ("program",)),
    "exception": (("EXCEPTION",), ("exception",)),
}


def map_dump_columns(desc: dict, sample: dict) -> dict[str, str]:
    columns = {}
    keys = list(sample.keys()) if sample else list(desc.keys())
    for semantic, (names, words) in DUMP_FIELDS.items():
        hit = next((k for k in keys if k.upper() in names), None)
        if hit is None:
            for k in keys:
                text = str(desc.get(k) or "").lower()
                if text and any(w in text for w in words) and k not in columns.values():
                    if semantic in ("date", "time") and "stamp" in text:
                        continue
                    hit = k
                    break
        if hit:
            columns[semantic] = hit
    return columns


def parse_dump_log(result: dict, zone) -> list[dict]:
    from ..tabular import parse_sap_datetime
    rows = _rows(result, "ET_E2E_LOG")
    if not rows:
        return []
    cols = map_dump_columns(result.get("ES_E2E_LOG_STRUCT_DESC") or {}, rows[0])
    out = []
    for r in rows:
        g = lambda k: str(r.get(cols.get(k, ""), "") or "").strip()  # noqa: E731
        at = None
        if g("timestamp")[:14].isdigit():
            at = parse_sap_datetime(g("timestamp")[:14], "", zone)
        if at is None and g("date"):
            at = parse_sap_datetime(g("date").replace("-", ""), g("time").replace(":", ""), zone)
        if at is None:
            continue
        out.append({"at": at, "error": g("error") or "?", "user": g("user") or None, "host": g("host") or None,
                    "client": g("client") or None, "program": g("program") or None})
    return out


def collect(client, zone, since: datetime | None = None) -> dict:
    """System info, server list, ST22 dumps (24 h) and SM37 jobs (7 days + running) with per-call diagnostics."""
    now = datetime.now(timezone.utc)
    diagnostics: list[dict] = []
    out: dict = {"collected_at": now, "diagnostics": diagnostics}

    def step(name, fn):
        try:
            value = fn()
            diagnostics.append({"call": name, "ok": True, "rows": len(value) if isinstance(value, list) else None})
            return value
        except CollectorError as exc:
            diagnostics.append({"call": name, "ok": False, "error": str(exc)})
            return None

    info = step("RFC_SYSTEM_INFO", lambda: client.call("RFC_SYSTEM_INFO").get("RFCSI_EXPORT") or {})
    if info is None:  # nothing else can work
        raise CollectorError(diagnostics[-1]["error"])
    out["system"] = {"sid": info.get("RFCSYSID"), "release": info.get("RFCSAPRL"), "database": info.get("RFCDBSYS"),
                     "host": info.get("RFCHOST"), "kernel": info.get("RFCKERNRL")}
    servers = step("TH_SERVER_LIST", lambda: _rows(client.call("TH_SERVER_LIST"), "LIST"))
    if servers is not None:
        out["servers"] = [{"name": str(s.get("NAME") or "").strip(), "host": str(s.get("HOST") or "").strip(),
                           "service": str(s.get("SERV") or "").strip()} for s in servers]

    dump_from = since or now - timedelta(hours=24)
    df, tf = _d(dump_from, zone)
    dt_, tt = _d(now, zone)
    dumps = step("/SDF/GET_DUMP_LOG", lambda: parse_dump_log(
        client.call("/SDF/GET_DUMP_LOG", DATE_FROM=df, TIME_FROM=tf, DATE_TO=dt_, TIME_TO=tt), zone))
    if dumps is not None:
        out["dumps"] = dumps

    def jobs():
        logon = client.call("BAPI_XMI_LOGON", EXTCOMPANY=XBP_COMPANY, EXTPRODUCT=XBP_PRODUCT, INTERFACE="XBP", VERSION="2.0")
        if _bapi_ok(logon):
            raise CollectorError(f"BAPI_XMI_LOGON: {_bapi_ok(logon)}")
        try:
            jf, _ = _d(now - timedelta(days=7), zone)
            jt, _ = _d(now, zone)
            result = client.call("BAPI_XBP_JOB_SELECT", EXTERNAL_USER_NAME=XBP_PRODUCT[:16], SELECTION="AL",
                                 JOB_SELECT_PARAM={"JOBNAME": "*", "USERNAME": "*", "FROM_DATE": jf, "FROM_TIME": "000000",
                                                   "TO_DATE": jt, "TO_TIME": "235959", "SCHEDUL": "X", "READY": "X",
                                                   "RUNNING": "X", "FINISHED": "X", "ABORTED": "X"})
            if _bapi_ok(result) and not _rows(result, "SELECTED_JOBS"):
                message = _bapi_ok(result)
                if "no job" in message.lower():  # an empty selection is not an error
                    return []
                raise CollectorError(f"BAPI_XBP_JOB_SELECT: {message}")
            return _rows(result, "SELECTED_JOBS")
        finally:
            try:
                client.call("BAPI_XMI_LOGOFF", INTERFACE="XBP")
            except CollectorError:
                pass
    selected = step("BAPI_XBP_JOB_SELECT", jobs)
    if selected is not None:
        out["jobs"] = [{k: ("" if v is None else str(v)) for k, v in row.items()} for row in selected]
    return out
