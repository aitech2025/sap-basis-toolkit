"""SAP HANA SQL collector (read-only, via SAP's hdbcli driver).

Database health comes from HANA monitoring views. When an ABAP schema is configured (e.g.
SAPHANADB / SAPABAP1 on S/4HANA, BW/4HANA or any NetWeaver ABAP on HANA) the collector also
reads ABAP runtime tables read-only: short dumps (SNAP), update errors (VBHDR), tRFC (ARFCSSTATE),
qRFC (TRFCQOUT/TRFCQIN), IDocs (EDIDC), background jobs (TBTCO), import history (TPALOG) and
table sizes (M_CS_TABLES).

Use a dedicated technical database user with only SELECT on the monitoring views and on these
ABAP tables (never the SAP schema owner). Direct SQL bypasses ABAP authorisations, so grant it to
the monitoring user only. Every query runs independently; a failing query (missing privilege,
different column on your release) is reported in the collector diagnostics and the rest continue.
Queries can be replaced per release through QUERY_OVERRIDES_FILE.
"""
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from ... import config
from ..target_policy import TargetPolicyError, resolve_approved_addresses
from . import CollectorError, validate_port

logger = logging.getLogger(__name__)
SCHEMA_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")

# name: (sql, needs_abap_schema, experimental). {schema} is substituted with the quoted ABAP schema.
QUERIES: dict[str, tuple[str, bool, bool]] = {
    "version": ("SELECT VERSION FROM M_DATABASE", False, False),
    "memory": ("SELECT SUM(INSTANCE_TOTAL_MEMORY_USED_SIZE) AS USED, SUM(ALLOCATION_LIMIT) AS LIMIT "
               "FROM M_HOST_RESOURCE_UTILIZATION", False, False),
    "cpu": ("SELECT AVG(CPU) AS CPU FROM M_LOAD_HISTORY_HOST WHERE TIME = (SELECT MAX(TIME) FROM M_LOAD_HISTORY_HOST)",
            False, True),
    "disk": ("SELECT SUM(USED_SIZE) AS USED, SUM(TOTAL_SIZE) AS TOTAL FROM M_DISKS WHERE USAGE_TYPE = 'DATA'", False, False),
    "alerts": ("SELECT ALERT_ID, ALERT_RATING, ALERT_DETAILS, ALERT_TIMESTAMP FROM _SYS_STATISTICS.STATISTICS_CURRENT_ALERTS",
               False, False),
    "backups": ("SELECT ENTRY_TYPE_NAME, MAX(SYS_END_TIME) AS LAST_END FROM M_BACKUP_CATALOG "
                "WHERE STATE_NAME = 'successful' GROUP BY ENTRY_TYPE_NAME", False, False),
    "replication": ("SELECT HOST, PORT, SECONDARY_HOST, REPLICATION_MODE, REPLICATION_STATUS, REPLICATION_STATUS_DETAILS "
                    "FROM M_SERVICE_REPLICATION", False, False),
    "services": ("SELECT HOST, SERVICE_NAME, ACTIVE_STATUS FROM M_SERVICES", False, False),
    # These views are optional and release/privilege dependent; failures are diagnostics, not collector-wide failures.
    "active_statements": ("SELECT COUNT(*) AS ACTIVE_COUNT, SUM(CASE WHEN DURATION_MICROSEC >= 30000000 THEN 1 ELSE 0 END) AS LONG_RUNNING, MAX(DURATION_MICROSEC) AS MAX_DURATION_US FROM M_ACTIVE_STATEMENTS", False, True),
    "service_threads": ("SELECT COUNT(*) AS THREAD_COUNT, SUM(CASE WHEN THREAD_STATE = 'WAITING' THEN 1 ELSE 0 END) AS WAITING_THREADS FROM M_SERVICE_THREADS", False, True),
    "expensive_statements": ("SELECT COUNT(*) AS STATEMENT_COUNT, MAX(DURATION_MICROSEC) AS MAX_DURATION_US FROM M_EXPENSIVE_STATEMENTS WHERE START_TIME >= ADD_SECONDS(CURRENT_TIMESTAMP, -3600)", False, True),
    "table_sizes": ("SELECT TABLE_NAME, SUM(MEMORY_SIZE_IN_TOTAL) AS MEMORY_SIZE_IN_TOTAL, SUM(RECORD_COUNT) AS RECORD_COUNT "
                    "FROM M_CS_TABLES WHERE SCHEMA_NAME = ? GROUP BY TABLE_NAME ORDER BY 2 DESC LIMIT 300", True, False),
    "dumps": ("SELECT DATUM, UZEIT, AHOST, UNAME, MANDT, FLIST FROM {schema}.SNAP WHERE SEQNO = '000' AND DATUM >= ?",
              True, False),
    "updates": ("SELECT VBKEY, VBUSR, VBTCODE, VBREPORT, VBDATE, VBRC FROM {schema}.VBHDR WHERE VBRC <> 0", True, True),
    "trfc": ("SELECT ARFCDEST, ARFCSTATE, ARFCFNAM, ARFCUSER, ARFCDATUM, ARFCUZEIT FROM {schema}.ARFCSSTATE "
             "WHERE ARFCSTATE IN ('SYSFAIL', 'CPICERR', 'ANORETRY', 'SYSLOAD', 'ARETRY')", True, False),
    "qrfc_out": ("SELECT QNAME, DEST, QSTATE, COUNT(*) AS ENTRIES FROM {schema}.TRFCQOUT GROUP BY QNAME, DEST, QSTATE",
                 True, False),
    "qrfc_in": ("SELECT QNAME, QSTATE, COUNT(*) AS ENTRIES FROM {schema}.TRFCQIN GROUP BY QNAME, QSTATE", True, True),
    "idoc_errors": ("SELECT DOCNUM, STATUS, MESTYP, DIRECT, CREDAT, CRETIM, SNDPRN, RCVPRN FROM {schema}.EDIDC "
                    "WHERE CREDAT >= ? AND STATUS IN ('02','04','05','25','26','29','51','56','60','61','63','65')",
                    True, False),
    "idoc_total": ("SELECT COUNT(*) AS TOTAL FROM {schema}.EDIDC WHERE CREDAT >= ?", True, False),
    "jobs": ("SELECT JOBNAME, JOBCOUNT, SDLUNAME, STATUS, STRTDATE, STRTTIME, ENDDATE, ENDTIME FROM {schema}.TBTCO "
             "WHERE STRTDATE >= ? OR STATUS = 'R'", True, False),
    "imports": ("SELECT TRKORR, RETCODE, TRTIME FROM {schema}.TPALOG WHERE TRTIME >= ?", True, True),
}


def query_catalogue() -> dict[str, tuple[str, bool, bool]]:
    catalogue = dict(QUERIES)
    if config.QUERY_OVERRIDES_FILE:
        try:
            with open(config.QUERY_OVERRIDES_FILE, encoding="utf-8") as fh:
                overrides = json.load(fh).get("hana", {})
            for name, sql in overrides.items():
                if name in catalogue and isinstance(sql, str):
                    _, needs, _ = catalogue[name]
                    catalogue[name] = (sql, needs, False)
        except (OSError, ValueError):
            logger.exception("Could not read QUERY_OVERRIDES_FILE")
    return catalogue


class HanaClient:
    """Thin wrapper over hdbcli so the collector can be tested with a fake client."""

    def __init__(self, host: str, port: int, user: str, password: str, use_tls: bool, verify_tls: bool):
        try:
            from hdbcli import dbapi
        except ImportError as exc:
            raise CollectorError("SAP HANA client (hdbcli) is not installed in this deployment; "
                                 "build the image with HANA_CLIENT=true (SAP Developer License)") from exc
        try:
            address = resolve_approved_addresses(host, port, check_port=False)[0]
        except TargetPolicyError as exc:
            raise CollectorError(f"Blocked by outbound policy: {exc}") from exc
        options = {"encrypt": use_tls, "sslValidateCertificate": verify_tls and use_tls,
                   "communicationTimeout": int(config.COLLECTOR_TIMEOUT_SECONDS * 1000),
                   "connectTimeout": int(config.COLLECTOR_TIMEOUT_SECONDS * 1000)}
        if use_tls and verify_tls:
            options["sslHostNameInCertificate"] = host
        try:
            self.connection = dbapi.connect(address=address, port=port, user=user, password=password, **options)
        except Exception as exc:  # hdbcli raises dbapi.Error subclasses
            raise CollectorError(f"HANA connection failed: {exc}") from exc

    def query(self, name: str, sql: str, params: tuple = ()) -> list[dict]:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            columns = [d[0].upper() for d in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def close(self) -> None:
        try:
            self.connection.close()
        except Exception:
            pass


def _ts(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)  # HANA SYS_* times are UTC
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _dump_error(flist) -> str:
    """Runtime error name from SNAP-FLIST (best effort: 'FC' + 3-digit length + name)."""
    text = flist.decode("utf-8", "replace") if isinstance(flist, (bytes, bytearray)) else str(flist or "")
    match = re.search(r"FC(\d{3})([A-Z0-9_/]+)", text)
    if match:
        return match.group(2)[: int(match.group(1))] or "unknown"
    return "unknown"


def collect(client, abap_schema: str | None, zone) -> dict:
    """Run every query; returns raw results per query plus diagnostics."""
    from ..tabular import parse_sap_datetime

    if abap_schema and not SCHEMA_RE.match(abap_schema):
        raise CollectorError("ABAP schema must be an uppercase identifier, e.g. SAPHANADB")
    now = datetime.now(timezone.utc)
    since_day = (now - timedelta(days=1)).astimezone(zone).strftime("%Y%m%d")
    since_week = (now - timedelta(days=7)).astimezone(zone).strftime("%Y%m%d")
    params = {"table_sizes": (abap_schema,), "dumps": (since_day,), "idoc_errors": (since_day,),
              "idoc_total": (since_day,), "jobs": (since_week,), "imports": (since_week + "000000",)}
    results, diagnostics = {}, []
    for name, (sql, needs_schema, experimental) in query_catalogue().items():
        if needs_schema and not abap_schema:
            continue
        statement = sql.replace("{schema}", f'"{abap_schema}"') if abap_schema else sql
        try:
            rows = client.query(name, statement, params.get(name, ()))
            results[name] = rows
            diagnostics.append({"call": name, "ok": True, "rows": len(rows), "experimental": experimental})
        except Exception as exc:
            diagnostics.append({"call": name, "ok": False, "error": str(exc)[:300], "experimental": experimental})

    out: dict = {"diagnostics": diagnostics, "collected_at": now}
    hana: dict = {}
    if results.get("version"):
        hana["version"] = results["version"][0].get("VERSION")
    if results.get("memory"):
        m = results["memory"][0]
        if m.get("LIMIT"):
            hana["memory"] = {"used": float(m["USED"] or 0), "limit": float(m["LIMIT"])}
    if results.get("cpu") and results["cpu"][0].get("CPU") is not None:
        hana["cpu_pct"] = round(float(results["cpu"][0]["CPU"]), 1)
    if results.get("disk"):
        d = results["disk"][0]
        if d.get("TOTAL"):
            hana["disk"] = {"used": float(d["USED"] or 0), "total": float(d["TOTAL"])}
    if "alerts" in results:
        hana["alerts"] = [{"id": a.get("ALERT_ID"), "rating": a.get("ALERT_RATING"), "details": a.get("ALERT_DETAILS"),
                           "at": _ts(a.get("ALERT_TIMESTAMP")).isoformat() if _ts(a.get("ALERT_TIMESTAMP")) else None}
                          for a in results["alerts"]]
    if "backups" in results:
        hana["backups"] = {str(b["ENTRY_TYPE_NAME"]).lower(): _ts(b["LAST_END"]) for b in results["backups"]}
    if "replication" in results:
        hana["replication"] = [{"host": r.get("HOST"), "port": r.get("PORT"), "secondary": r.get("SECONDARY_HOST"),
                                "mode": r.get("REPLICATION_MODE"), "status": r.get("REPLICATION_STATUS"),
                                "details": r.get("REPLICATION_STATUS_DETAILS")} for r in results["replication"]]
    if "services" in results:
        hana["services"] = [{"host": s.get("HOST"), "service": s.get("SERVICE_NAME"), "active": s.get("ACTIVE_STATUS")}
                            for s in results["services"]]
    if results.get("active_statements"):
        row = results["active_statements"][0]
        hana["active_statements"] = {"count": int(row.get("ACTIVE_COUNT") or 0),
                                     "long_running": int(row.get("LONG_RUNNING") or 0),
                                     "max_duration_s": round(float(row.get("MAX_DURATION_US") or 0) / 1_000_000, 2)}
    if results.get("service_threads"):
        row = results["service_threads"][0]
        hana["service_threads"] = {"count": int(row.get("THREAD_COUNT") or 0),
                                   "waiting": int(row.get("WAITING_THREADS") or 0)}
    if results.get("expensive_statements"):
        row = results["expensive_statements"][0]
        hana["expensive_statements_1h"] = {"count": int(row.get("STATEMENT_COUNT") or 0),
                                           "max_duration_s": round(float(row.get("MAX_DURATION_US") or 0) / 1_000_000, 2)}
    out["hana"] = hana

    if "dumps" in results:
        out["dumps"] = [{"at": parse_sap_datetime(str(r["DATUM"]), str(r["UZEIT"]), zone), "error": _dump_error(r.get("FLIST")),
                         "user": r.get("UNAME"), "host": r.get("AHOST"), "client": r.get("MANDT")} for r in results["dumps"]]
    if "updates" in results:
        out["updates"] = [{"at": parse_sap_datetime(str(r.get("VBDATE") or "")[:8], str(r.get("VBDATE") or "")[8:14], zone),
                           "user": r.get("VBUSR"), "tcode": r.get("VBTCODE"), "report": r.get("VBREPORT"),
                           "state": str(r.get("VBRC")), "key": r.get("VBKEY")} for r in results["updates"]]
    if "trfc" in results:
        out["trfc"] = [{"dest": r.get("ARFCDEST"), "state": r.get("ARFCSTATE"), "function": r.get("ARFCFNAM"),
                        "user": r.get("ARFCUSER"), "at": parse_sap_datetime(str(r.get("ARFCDATUM") or ""), str(r.get("ARFCUZEIT") or ""), zone)}
                       for r in results["trfc"]]
    if "qrfc_out" in results or "qrfc_in" in results:
        out["qrfc"] = [{"direction": "out", "qname": r.get("QNAME"), "dest": r.get("DEST"), "state": (r.get("QSTATE") or "").upper(),
                        "entries": int(r.get("ENTRIES") or 0), "oldest": None} for r in results.get("qrfc_out", [])] + \
                      [{"direction": "in", "qname": r.get("QNAME"), "dest": None, "state": (r.get("QSTATE") or "").upper(),
                        "entries": int(r.get("ENTRIES") or 0), "oldest": None} for r in results.get("qrfc_in", [])]
    if "idoc_errors" in results:
        out["idocs"] = {"errors": [{"docnum": str(r.get("DOCNUM") or "").lstrip("0"), "status": str(r.get("STATUS")).zfill(2),
                                    "mestyp": r.get("MESTYP"), "direction": r.get("DIRECT"),
                                    "partner": r.get("RCVPRN") if str(r.get("DIRECT")) == "1" else r.get("SNDPRN"),
                                    "at": parse_sap_datetime(str(r.get("CREDAT")), str(r.get("CRETIM")), zone)}
                                   for r in results["idoc_errors"]],
                        "total": int(results["idoc_total"][0]["TOTAL"]) if results.get("idoc_total") else None}
    if "jobs" in results:
        out["jobs"] = results["jobs"]
    if "imports" in results:
        out["imports"] = results["imports"]
    if "table_sizes" in results:
        out["table_sizes"] = results["table_sizes"]
    return out
