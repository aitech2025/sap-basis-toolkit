"""Demo/test simulator: synthetic sapcontrol SOAP responses and HANA query results.

Enabled only with SIMULATOR_ENABLED=true, for collectors whose host is ``simulator``. Values vary
per system and per 5-minute bucket so charts and incidents behave like a live landscape. Never
enable in production: the data is fake.
"""
import random
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

SIM_HOST = "simulator"


def _rng(seed: str) -> random.Random:
    bucket = int(datetime.now(timezone.utc).timestamp() // 300)
    return random.Random(f"{seed}:{bucket}")


def _items(tag: str, rows: list[dict]) -> str:
    body = "".join("<item>" + "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in row.items()) + "</item>" for row in rows)
    return f"<{tag}>{body}</{tag}>"


def _envelope(method: str, inner: str) -> str:
    return ('<?xml version="1.0" encoding="utf-8"?><SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" '
            f'xmlns:SAPControl="urn:SAPControl"><SOAP-ENV:Body><SAPControl:{method}Response>{inner}'
            f'</SAPControl:{method}Response></SOAP-ENV:Body></SOAP-ENV:Envelope>')


def profile(seed: str) -> dict:
    """Stable per-system personality: some systems are calm, some are busy or degraded."""
    r = random.Random(seed)
    return {"busy": r.random(), "fragile": r.random() < 0.35, "instances": 2 + r.randint(0, 1)}


def sapcontrol_transport(seed: str, java: bool = False):
    p = profile(seed)
    # central services on instance 10 (ports 51013/51014), application servers on 00, 01, 02
    down_nr = p["instances"] - 1 if p["fragile"] and _rng(f"{seed}:down").random() < 0.5 else None

    def transport(endpoint, method: str, body: str) -> str:
        r = _rng(f"{seed}:{endpoint.port}:{method}")
        now = datetime.now(timezone.utc)
        instance_nr = int(str(endpoint.port)[1:3])
        if method == "GetSystemInstanceList":
            rows = [{"hostname": "sapscs" if java else "sapascs", "instanceNr": 10, "httpPort": 51013, "httpsPort": 51014,
                     "startPriority": "1", "features": "MESSAGESERVER|ENQUE", "dispstatus": "SAPControl-GREEN"}]
            for i in range(p["instances"]):
                down = i == down_nr
                rows.append({"hostname": f"sapapp{i + 1}", "instanceNr": i, "httpPort": int(f"50{i}13"), "httpsPort": int(f"50{i}14"),
                             "startPriority": "3", "features": "J2EE|IGS" if java else "ABAP|GATEWAY|ICMAN|IGS",
                             "dispstatus": "SAPControl-GRAY" if down else "SAPControl-GREEN"})
            return _envelope(method, _items("instance", rows))
        if method == "GetProcessList":
            if instance_nr == 10:
                procs = [("msg_server", "MessageServer"), ("enq_server", "Enqueue Server 2")]
            elif java:
                procs = [("jstart", "J2EE Server"), ("igswd_mt", "IGS Watchdog"), ("icman", "ICM")]
            else:
                procs = [("disp+work", "Dispatcher"), ("igswd_mt", "IGS Watchdog"), ("gwrd", "Gateway"), ("icman", "ICM")]
            stopped = instance_nr == down_nr
            return _envelope(method, _items("process", [
                {"name": n, "description": d, "dispstatus": "SAPControl-GRAY" if stopped else "SAPControl-GREEN",
                 "textstatus": "Stopped" if stopped else "Running",
                 "starttime": "" if stopped else (now - timedelta(days=12)).strftime("%Y %m %d %H:%M:%S"),
                 "elapsedtime": "" if stopped else "288:10:02", "pid": "" if stopped else r.randint(1000, 99999)} for n, d in procs]))
        if method == "ABAPGetWPTable":
            rows, no = [], 0
            for typ, count in (("DIA", 16), ("UPD", 3), ("UP2", 1), ("BTC", 6), ("SPO", 1)):
                for _ in range(count):
                    load = 0.25 + p["busy"] * 0.6 if typ in ("DIA", "BTC") else 0.1
                    running = r.random() < load
                    long_run = running and typ in ("DIA", "BTC") and p["fragile"] and r.random() < 0.15
                    rows.append({"No": no, "Typ": typ, "Pid": r.randint(1000, 99999), "Status": "Run" if running else "Wait",
                                 "Reason": "", "Start": "yes", "Err": r.choice([0, 0, 0, 1]), "Sem": "", "Cpu": "",
                                 "Time": r.randint(2000, 7200) if long_run else (r.randint(1, 120) if running else ""),
                                 "Program": r.choice(["SAPLSBAL", "RSBTCRTE", "SAPMV45A", "SAPLMEGUI", "CL_HTTP_SERVER"]) if running else "",
                                 "Client": "100" if running else "", "User": r.choice(["JSMITH", "BATCH_FI", "MMUELLER", "RFC_PI"]) if running else "",
                                 "Action": r.choice(["Sequential Read", "Direct Read", "Update", ""]) if running else "",
                                 "Table": r.choice(["VBAK", "BKPF", "MARA", ""]) if running else ""})
                    no += 1
            return _envelope(method, _items("workprocess", rows))
        if method == "ABAPReadSyslog":
            msgs = [("Information", "Q0I", "Operating system call recv failed (error no. 104)"),
                    ("Error", "AB0", "Run-time error \"TIME_OUT\" occurred"),
                    ("Error", "D01", "Transaction Canceled 00 671 ( ARCHIVE_INFO_NOT_FOUND )"),
                    ("Warning", "R49", "Communication error, CPIC return code 017, SAP return code 223"),
                    ("Information", "US1", "User JSMITH locked due to incorrect logon")]
            count = r.randint(2, 6) + (r.randint(5, 15) if p["fragile"] else 0)
            rows = []
            for _ in range(count):
                sev, mno, text = r.choice(msgs)
                at = now - timedelta(minutes=r.randint(0, 55))
                rows.append({"Time": at.strftime("%Y-%m-%d %H:%M:%S"), "Severity": sev, "MNo": mno, "Text": text,
                             "User": r.choice(["SAPSYS", "JSMITH", "BATCH_FI"]), "Client": "100", "Program": "SAPMSSY1"})
            return _envelope(method, _items("log", rows))
        return _envelope(method, "")
    return transport


class SimulatedHana:
    """Answers the HANA collector's named queries with plausible data."""

    def __init__(self, seed: str):
        self.seed = seed
        self.p = profile(seed)
        self.r = _rng(f"{seed}:hana")
        self.now = datetime.now(timezone.utc)

    def query(self, name: str, sql: str, params: tuple = ()) -> list[dict]:
        r, p, now = self.r, self.p, self.now
        gb = 1024 ** 3
        if name == "version":
            return [{"VERSION": "2.00.079.00.1720500000"}]
        if name == "memory":
            limit = 1024 * gb
            return [{"USED": limit * (0.55 + p["busy"] * 0.35 + r.random() * 0.05), "LIMIT": limit}]
        if name == "cpu":
            return [{"CPU": 15 + p["busy"] * 60 + r.random() * 10}]
        if name == "disk":
            total = 4096 * gb
            return [{"USED": total * (0.6 + p["busy"] * 0.25), "TOTAL": total}]
        if name == "alerts":
            rows = [{"ALERT_ID": 31, "ALERT_RATING": 2, "ALERT_DETAILS": "License expires in 85 days", "ALERT_TIMESTAMP": now}]
            if p["fragile"]:
                rows.append({"ALERT_ID": 65, "ALERT_RATING": 4, "ALERT_DETAILS": "Runtime of the log backups currently running is 3700 seconds",
                             "ALERT_TIMESTAMP": now - timedelta(minutes=20)})
            return rows
        if name == "backups":
            data_age = 30 if p["fragile"] else 8
            return [{"ENTRY_TYPE_NAME": "complete data backup", "LAST_END": now - timedelta(hours=data_age + r.random())},
                    {"ENTRY_TYPE_NAME": "log backup", "LAST_END": now - timedelta(minutes=r.randint(2, 14))}]
        if name == "replication":
            return [{"HOST": "hdbprim", "PORT": 30003, "SECONDARY_HOST": "hdbsec", "REPLICATION_MODE": "SYNC",
                     "REPLICATION_STATUS": "ACTIVE", "REPLICATION_STATUS_DETAILS": ""}]
        if name == "services":
            return [{"HOST": "hdbprim", "SERVICE_NAME": s, "ACTIVE_STATUS": "YES"}
                    for s in ("nameserver", "indexserver", "xsengine", "compileserver", "preprocessor")]
        if name == "table_sizes":
            base = {"ACDOCA": 380, "BALDAT": 95, "CDPOS": 70, "EDI40": 55, "SOFFCONT1": 48, "BALHDR": 12, "DBTABLOG": 22,
                    "MATDOC": 140, "VBAP": 30, "EDIDC": 8}
            days = (now - datetime(2026, 1, 1, tzinfo=timezone.utc)).days
            return [{"TABLE_NAME": t, "MEMORY_SIZE_IN_TOTAL": (s + days * s * 0.002) * gb, "RECORD_COUNT": int(s * 1_000_000)}
                    for t, s in base.items()]
        if name == "dumps":
            errors = ["TIME_OUT", "DBSQL_DUPLICATE_KEY_ERROR", "MESSAGE_TYPE_X", "TSV_TNEW_PAGE_ALLOC_FAILED", "CONVT_NO_NUMBER"]
            count = r.randint(5, 25) + (r.randint(20, 60) if p["fragile"] else 0)
            rows = []
            for _ in range(count):
                at = (now - timedelta(minutes=r.randint(0, 1400)))
                err = r.choice(errors)
                rows.append({"DATUM": at.strftime("%Y%m%d"), "UZEIT": at.strftime("%H%M%S"), "AHOST": r.choice(["sapapp1_S4P_00", "sapapp2_S4P_01"]),
                             "UNAME": r.choice(["JSMITH", "BATCH_FI", "RFC_PI", "MMUELLER"]), "MANDT": "100",
                             "FLIST": f"FC{len(err):03d}{err}AP012SAPMV45A"})
            return rows
        if name == "updates":
            return [] if not p["fragile"] else [{"VBKEY": "5E3F0A", "VBUSR": "JSMITH", "VBTCODE": "VA01", "VBREPORT": "SAPMV45A",
                                                 "VBDATE": now.strftime("%Y%m%d%H%M%S"), "VBRC": 12}]
        if name == "trfc":
            if not p["fragile"]:
                return []
            return [{"ARFCDEST": "BWP_CLNT100", "ARFCSTATE": "SYSFAIL", "ARFCFNAM": "IDOC_INBOUND_ASYNCHRONOUS", "ARFCUSER": "ALEREMOTE",
                     "ARFCDATUM": now.strftime("%Y%m%d"), "ARFCUZEIT": (now - timedelta(minutes=i * 7)).strftime("%H%M%S")} for i in range(r.randint(2, 8))]
        if name == "qrfc_out":
            rows = [{"QNAME": "CSA_ORDER_0000001234", "DEST": "CRM_CLNT100", "QSTATE": "READY", "ENTRIES": r.randint(1, 30)}]
            if p["fragile"]:
                rows.append({"QNAME": "R3AD_MATERIAL000045", "DEST": "CRM_CLNT100", "QSTATE": "SYSFAIL", "ENTRIES": r.randint(50, 400)})
            return rows
        if name == "qrfc_in":
            return [{"QNAME": "BWQ_DELTA_2LIS_11", "QSTATE": "READY", "ENTRIES": r.randint(0, 12)}]
        if name == "idoc_errors":
            rows = []
            for i in range(r.randint(0, 6) + (r.randint(10, 40) if p["fragile"] else 0)):
                at = now - timedelta(minutes=r.randint(0, 1400))
                status = r.choice(["51", "51", "51", "56", "29", "02"])
                rows.append({"DOCNUM": f"{9000000 + i:016d}", "STATUS": status, "MESTYP": r.choice(["ORDERS", "DESADV", "INVOIC", "MATMAS"]),
                             "DIRECT": "2" if status in ("51", "56") else "1", "CREDAT": at.strftime("%Y%m%d"), "CRETIM": at.strftime("%H%M%S"),
                             "SNDPRN": "VENDOR01", "RCVPRN": "CUST01"})
            return rows
        if name == "idoc_total":
            return [{"TOTAL": r.randint(800, 5000)}]
        if name == "jobs":
            rows = []
            for i, (job, fail) in enumerate((("SAP_COLLECTOR_PERFMON_SWNCCOLL", 0.0), ("Z_FI_PAYMENT_RUN", 0.4 if p["fragile"] else 0.05),
                                             ("SAP_REORG_SPOOL", 0.0), ("Z_MM_MRP_RUN", 0.1))):
                for d in range(3):
                    start = now - timedelta(days=d, hours=i + 1)
                    status = "A" if r.random() < fail else "F"
                    rows.append({"JOBNAME": job, "JOBCOUNT": f"{start:%H%M%S}{i}{d}", "SDLUNAME": "BATCH", "STATUS": status,
                                 "STRTDATE": start.strftime("%Y%m%d"), "STRTTIME": start.strftime("%H%M%S"),
                                 "ENDDATE": start.strftime("%Y%m%d"), "ENDTIME": (start + timedelta(minutes=5)).strftime("%H%M%S")})
            return rows
        if name == "imports":
            return [{"TRKORR": f"S4DK90{1200 + d:04d}", "RETCODE": "0008" if p["fragile"] and d == 0 else "0000",
                     "TRTIME": (now - timedelta(days=d, hours=3)).strftime("%Y%m%d%H%M%S")} for d in range(4)]
        return []

    def close(self) -> None:
        pass
