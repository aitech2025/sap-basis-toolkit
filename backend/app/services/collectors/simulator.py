"""Demo/test simulator: synthetic sapcontrol SOAP responses and HANA query results.

Enabled only with SIMULATOR_ENABLED=true, for collectors whose host is ``simulator``. Values vary
per system and per 5-minute bucket so charts and incidents behave like a live landscape. Never
enable in production: the data is fake.
"""
import random
import threading
import time
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


_STATE: dict[str, dict] = {}
_STATE_LOCK = threading.Lock()
# Seconds for simulated state transitions; tests shrink this.
TRANSITION_SCALE = 1.0


def is_sim(host: str | None) -> bool:
    """'simulator' or 'simulator-<sid>' (the SID variant drives discovery demos)."""
    return bool(host) and (host == SIM_HOST or host.startswith(SIM_HOST + "-"))


def seed_for(host: str, system_seed: str) -> str:
    """Named simulator hosts keep one identity across discovery and collection."""
    return host.lower() if host.startswith(SIM_HOST + "-") else system_seed


def reset_state() -> None:
    with _STATE_LOCK:
        _STATE.clear()


def sim_sid(host: str) -> str:
    return host.split("-", 1)[1].upper()[:3] if "-" in host else "SIM"


def sim_kind(sid: str) -> str:
    """Product family the simulated SID pretends to be."""
    if sid.startswith(("EP", "PI", "J")):
        return "java"
    if sid.startswith("EC"):
        return "ecc"
    if sid.startswith("BW"):
        return "bw4"
    return "s4"


def is_db_seed(seed: str) -> bool:
    return seed.startswith(SIM_HOST + "-") and seed.endswith("-db")


def db_host_for(sid: str) -> str:
    return f"{SIM_HOST}-{sid.lower()}-db"


def db_running(app_seed: str) -> bool | None:
    """State of the simulated HANA belonging to an application seed (None: never contacted)."""
    db_seed = f"{app_seed}-db"
    if db_seed not in _STATE:
        return None
    return all(v == "GREEN" for v in _state(db_seed, False)["status"].values())


def _layout(seed: str, java: bool) -> list[dict]:
    if is_db_seed(seed):
        sid = sim_sid(seed)
        return [{"nr": "00", "hostname": f"hdb{sid.lower()}", "name": "HDB00", "features": "HDB|HDB_WORKER", "prio": "0.3"}]
    p = profile(seed)
    central = "SCS" if java else "ASCS"
    rows = [
        {"nr": "10", "hostname": f"sap{central.lower()}", "name": f"{central}10", "features": "MESSAGESERVER|ENQUE", "prio": "1"},
        {"nr": "11", "hostname": "sapers", "name": "ERS11", "features": "ENQREP", "prio": "0.5"},
    ]
    for i in range(p["instances"]):
        rows.append({"nr": f"{i:02d}", "hostname": f"sapapp{i + 1}", "name": f"{'J' if java else 'D'}{i:02d}",
                     "features": "J2EE|IGS" if java else "ABAP|GATEWAY|ICMAN|IGS", "prio": "3"})
    return rows


def _state(seed: str, java: bool) -> dict:
    with _STATE_LOCK:
        if seed not in _STATE:
            p = profile(seed)
            down_nr = (f"{p['instances'] - 1:02d}" if p["fragile"] and not is_db_seed(seed)
                       and _rng(f"{seed}:down").random() < 0.5 else None)
            _STATE[seed] = {"layout": _layout(seed, java),
                            "status": {row["nr"]: ("GRAY" if row["nr"] == down_nr else "GREEN") for row in _layout(seed, java)},
                            "pending": []}
        state = _STATE[seed]
        now = time.monotonic()
        due = sorted((e for e in state["pending"] if e[0] <= now), key=lambda e: e[0])
        for _, nr, status in due:
            state["status"][nr] = status
        state["pending"] = [e for e in state["pending"] if e[0] > now]
        return state


def _schedule(seed: str, java: bool, nrs: list[str], steps: list[tuple[float, str]]) -> None:
    state = _state(seed, java)
    with _STATE_LOCK:
        now = time.monotonic()
        state["pending"] = [e for e in state["pending"] if e[1] not in nrs]
        for nr in nrs:
            state["status"][nr] = "YELLOW"
            for delay, status in steps:
                state["pending"].append((now + delay * TRANSITION_SCALE, nr, status))


def _option_targets(layout: list[dict], body: str) -> list[str]:
    option = body.split("<options>", 1)[1].split("</options>", 1)[0] if "<options>" in body else "SAPControl-ALL-INSTANCES"
    def has(row, feats):
        return bool(set(row["features"].split("|")) & feats)
    selectors = {
        "SAPControl-ALL-INSTANCES": lambda r: True,
        "SAPControl-SCS-INSTANCES": lambda r: has(r, {"MESSAGESERVER", "ENQUE", "ENQREP"}),
        "SAPControl-DIALOG-INSTANCES": lambda r: has(r, {"ABAP", "J2EE"}),
        "SAPControl-ABAP-INSTANCES": lambda r: has(r, {"ABAP"}),
        "SAPControl-J2EE-INSTANCES": lambda r: has(r, {"J2EE"}),
        "SAPControl-ENQREP-INSTANCES": lambda r: has(r, {"ENQREP"}),
    }
    return [r["nr"] for r in layout if selectors.get(option, lambda r: True)(r)]


COMPONENTS = {
    "s4": [("SAP_BASIS", "758", "0002"), ("SAP_ABA", "75I", "0002"), ("S4CORE", "108", "0002"), ("SAP_UI", "758", "0004")],
    "ecc": [("SAP_BASIS", "750", "0027"), ("SAP_ABA", "750", "0027"), ("SAP_APPL", "618", "0024"), ("EA-FIN", "618", "0024")],
    "bw4": [("SAP_BASIS", "754", "0008"), ("DW4CORE", "200", "0012"), ("SAP_UI", "754", "0007")],
}
DBMS = {"s4": "hdb", "ecc": "ora", "bw4": "hdb", "java": "hdb"}


def sapcontrol_transport(seed: str, java: bool = False):
    p = profile(seed)
    named = seed.startswith(SIM_HOST + "-")
    sid = sim_sid(seed) if named else "SIM"
    kind = "hana" if is_db_seed(seed) else ("java" if java else sim_kind(sid if named else "S4"))
    java = java or kind == "java"

    def transport(endpoint, method: str, body: str) -> str:
        r = _rng(f"{seed}:{endpoint.port}:{method}")
        now = datetime.now(timezone.utc)
        instance_nr = str(endpoint.port)[1:3]
        state = _state(seed, java)
        layout = {row["nr"]: row for row in state["layout"]}
        if instance_nr not in layout:
            raise ConnectionError(f"Connection refused ({endpoint.host}:{endpoint.port})")
        row = layout[instance_nr]
        green = state["status"][instance_nr] == "GREEN"
        if method in ("Start", "Stop", "RestartInstance", "StartSystem", "StopSystem", "RestartSystem", "AccessCheck") \
                and not endpoint.username:
            return _fault("Permission denied")
        if method == "AccessCheck":
            return _envelope(method, "")
        if method == "GetSystemInstanceList":
            return _envelope(method, _items("instance", [
                {"hostname": x["hostname"], "instanceNr": int(x["nr"]), "httpPort": int(f"5{x['nr']}13"),
                 "httpsPort": int(f"5{x['nr']}14"), "startPriority": x["prio"], "features": x["features"],
                 "dispstatus": f"SAPControl-{state['status'][x['nr']]}"} for x in state["layout"]]))
        if method == "GetInstanceProperties":
            props = [("SAPSYSTEMNAME", f"{sid[:2]}H" if kind == "hana" else sid), ("SAPSYSTEM", instance_nr), ("INSTANCE_NAME", row["name"]),
                     ("SAPLOCALHOST", row["hostname"]), ("StartPriority", row["prio"])]
            return _envelope(method, "<properties>" + "".join(
                f"<item><property>{k}</property><propertytype>Attribute</propertytype><value>{v}</value></item>" for k, v in props)
                + "</properties>")
        if method == "ParameterValue":
            name = body.split("<parameter>", 1)[1].split("</parameter>", 1)[0] if "<parameter>" in body else ""
            value = {"dbms/type": DBMS.get(kind, "hdb"),
                     "SAPDBHOST": db_host_for(sid) if named and DBMS.get(kind, "hdb") == "hdb" else f"db{sid.lower()}",
                     "SAPSYSTEMNAME": sid}.get(name, "")
            return _envelope(method, f"<value>{value}</value>")
        if method == "ABAPGetComponentList":
            if "ABAP" not in row["features"] or not green:
                return _fault("ABAP component list not available")
            return _envelope(method, _items("component", [
                {"component": c, "release": rel, "patchlevel": pl, "componenttype": "S", "description": c}
                for c, rel, pl in COMPONENTS.get(kind, COMPONENTS["s4"])]))
        # Application servers cannot connect when the (simulated) database is down: they end RED.
        db_down = named and kind not in ("hana",) and db_running(seed) is False
        app_target = "RED" if db_down and ("ABAP" in row["features"] or "J2EE" in row["features"]) else "GREEN"
        if method in ("Start", "Stop", "RestartInstance"):
            steps = {"Start": [(3, app_target)], "Stop": [(3, "GRAY")],
                     "RestartInstance": [(2, "GRAY"), (5, app_target)]}[method]
            if kind == "hana":
                steps = {"Start": [(6, "GREEN")], "Stop": [(4, "GRAY")], "RestartInstance": [(4, "GRAY"), (10, "GREEN")]}[method]
            _schedule(seed, java, [instance_nr], steps)
            return _envelope(method, "")
        if method in ("StartSystem", "StopSystem", "RestartSystem"):
            targets = _option_targets(state["layout"], body)
            central = [n for n in targets if layout[n]["prio"] in ("0.5", "1")]
            apps = [n for n in targets if n not in central]
            if method == "StartSystem":
                _schedule(seed, java, central, [(2, "GREEN")])
                _schedule(seed, java, apps, [(5, "GREEN")])
            elif method == "StopSystem":
                _schedule(seed, java, apps, [(2, "GRAY")])
                _schedule(seed, java, central, [(4, "GRAY")])
            else:
                _schedule(seed, java, apps, [(2, "GRAY"), (8, "GREEN")])
                _schedule(seed, java, central, [(4, "GRAY"), (6, "GREEN")])
            return _envelope(method, "")
        if method == "GetProcessList":
            if kind == "hana":
                procs = [("hdbdaemon", "HDB Daemon"), ("hdbnameserver", "HDB Nameserver"), ("hdbindexserver", "HDB Indexserver-" + sid[:2] + "H"),
                         ("hdbcompileserver", "HDB Compileserver"), ("hdbpreprocessor", "HDB Preprocessor"), ("hdbwebdispatcher", "HDB Web Dispatcher")]
            elif "MESSAGESERVER" in row["features"]:
                procs = [("msg_server", "MessageServer"), ("enq_server", "Enqueue Server 2")]
            elif "ENQREP" in row["features"]:
                procs = [("enq_replicator", "Enqueue Replicator 2")]
            elif java:
                procs = [("jstart", "J2EE Server"), ("igswd_mt", "IGS Watchdog"), ("icman", "ICM")]
            else:
                procs = [("disp+work", "Dispatcher"), ("igswd_mt", "IGS Watchdog"), ("gwrd", "Gateway"), ("icman", "ICM")]
            status = state["status"][instance_nr]
            text = {"GREEN": "Running", "YELLOW": "Starting/stopping", "GRAY": "Stopped", "RED": "Stopped"}[status]
            return _envelope(method, _items("process", [
                {"name": n, "description": d, "dispstatus": f"SAPControl-{status}", "textstatus": text,
                 "starttime": (now - timedelta(days=12)).strftime("%Y %m %d %H:%M:%S") if green else "",
                 "elapsedtime": "288:10:02" if green else "", "pid": r.randint(1000, 99999) if green else ""} for n, d in procs]))
        if method == "ABAPGetWPTable":
            if not green:
                return _fault("DpAttachStartService failed")
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
            if not green:
                return _fault("DpAttachStartService failed")
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
        if method == "GetQueueStatistic":
            if "ABAP" not in row["features"] or not green:
                return _fault("DpAttachStartService failed")
            dia = r.randint(0, 3) + (r.randint(8, 30) if p["fragile"] and r.random() < 0.3 else 0)
            return _envelope(method, _items("queue", [
                {"Typ": t, "Now": now_, "High": max(now_, r.randint(5, 40)), "Max": 14000, "Writes": r.randint(10**5, 10**6),
                 "Reads": r.randint(10**5, 10**6)}
                for t, now_ in (("ABAP/NOWP", 0), ("ABAP/DIA", dia), ("ABAP/UPD", r.randint(0, 2)), ("ABAP/ENQ", 0),
                                ("ABAP/BTC", r.randint(0, 4)), ("ABAP/SPO", 0), ("ABAP/UP2", 0), ("ICM/Intern", 0))]))
        if method in ("GetAlertTree", "GetAlerts"):
            if "ABAP" not in row["features"] or not green:
                return _fault("GetAlertTree, OpenCCMSMemory failed")
            ctx = f"{row['hostname']}_{sid}_{instance_nr}"
            resp = int(350 + p["busy"] * 900 + (r.randint(800, 2500) if p["fragile"] and r.random() < 0.25 else r.randint(0, 200)))
            dumps_red = p["fragile"] and r.random() < 0.4
            t = now.strftime("%Y %m %d %H:%M:%S")
            tree = [(ctx, -1, "GREEN", ""), ("R3Services", 0, "GREEN", ""), ("Dialog", 1, "YELLOW" if resp >= 1500 else "GREEN", ""),
                    ("ResponseTime", 2, "RED" if resp >= 3000 else "YELLOW" if resp >= 1500 else "GREEN", f"{resp} msec"),
                    ("FrontEndResponseTime", 2, "GREEN", f"{int(resp * 1.3)} msec"),
                    ("UsersLoggedIn", 2, "GREEN", f"{r.randint(20, 400)} "), ("Background", 1, "GREEN", ""),
                    ("AbortedJobs", 6, "RED" if p["fragile"] and r.random() < 0.3 else "GREEN", "Job canceled" if p["fragile"] else ""),
                    ("R3Abap", 0, "RED" if dumps_red else "GREEN", ""),
                    ("Shortdumps", 8, "RED" if dumps_red else "GREEN", "TIME_OUT" if dumps_red else ""),
                    ("Update", 1, "GREEN", ""), ("UpdateStatus", 10, "GREEN", "Updates active"),
                    ("Buffers", 0, "GREEN", ""),
                    ("Program", 12, "GREEN", ""), ("HitRatio", 13, "GREEN", f"{r.uniform(97.5, 99.9):.1f} %"),
                    ("Swap", 13, "YELLOW" if p["fragile"] else "GREEN", f"{r.randint(1500, 6000) if p['fragile'] else r.randint(0, 300)} /day"),
                    ("GenericKey", 12, "GREEN", ""), ("HitRatio", 16, "GREEN", f"{r.uniform(98, 100):.1f} %"),
                    ("Swap", 16, "GREEN", f"{r.randint(0, 50)} /day")]
            if method == "GetAlertTree":
                return _envelope(method, _items("tree", [
                    {"name": n, "parent": par, "ActualValue": f"SAPControl-{c}", "description": d, "Time": t,
                     "AnalyseToolString": "", "VisibleLevel": 1, "HighAlertValue": f"SAPControl-{c}", "AlValue": f"SAPControl-{c}",
                     "Tid": f"{sid}\\{ctx}\\{n}"} for n, par, c, d in tree]))
            alerts = [{"Object": n, "Attribute": n, "Value": f"SAPControl-{c}", "Description": d or n,
                       "Time": (now - timedelta(hours=r.randint(0, 72))).strftime("%Y %m %d %H:%M:%S"), "Tid": n, "AId": str(i)}
                      for i, (n, par, c, d) in enumerate(tree) if c in ("RED", "YELLOW")]
            return _envelope(method, _items("alert", alerts))
        if method in ("EnqGetLockTable", "EnqGetStatistic"):
            if "ENQUE" not in row["features"] or not green:
                return _fault("EnqGetLockTable: no enqueue server in this instance")
            locks = []
            for i in range(r.randint(5, 40)):
                age = r.randint(0, 20) if not (p["fragile"] and i < 2) else r.randint(70, 400)
                at = now - timedelta(minutes=age)  # system timezone defaults to UTC
                locks.append({"lock-name": r.choice(["VBAK", "EKKO", "MARA", "BKPF", "ES_PROG"]), "lock-arg": f"100{r.randint(10**7, 10**8)}",
                              "lock-mode": r.choice(["E", "E", "S", "X"]),
                              "owner": at.strftime("%Y%m%d%H%M%S") + f"{r.randint(0, 999999):06d}000sap{sid.lower()}app1",
                              "owner-vb": "", "use-count-owner": 1, "use-count-owner-vb": 0, "client": "100",
                              "user": r.choice(["JSMITH", "BATCH_FI", "MMUELLER", "RFC_PI"]), "transaction": r.choice(["VA02", "ME22N", "SE38", ""]),
                              "object": "", "backup": "FALSE"})
            if method == "EnqGetLockTable":
                return _envelope(method, _items("lock", locks))
            n = len(locks)
            fields = {"owner-now": n, "owner-high": n + 30, "owner-max": 80000, "owner-state": "SAPControl-GREEN",
                      "arguments-now": n, "arguments-high": n + 40, "arguments-max": 80000, "arguments-state": "SAPControl-GREEN",
                      "locks-now": n, "locks-high": n + 60, "locks-max": 80000, "locks-state": "SAPControl-GREEN",
                      "enqueue-requests": r.randint(10**6, 10**7), "enqueue-rejects": r.randint(0, 5000), "enqueue-errors": 0,
                      "dequeue-requests": r.randint(10**6, 10**7), "dequeue-errors": 0, "lock-time": "12.5", "lock-wait-time": "0.6"}
            return _envelope(method, "<statistic>" + "".join(f"<{k}>{v}</{k}>" for k, v in fields.items()) + "</statistic>")
        return _envelope(method, "")
    return transport


def _fault(text: str) -> str:
    return ('<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"><SOAP-ENV:Body><SOAP-ENV:Fault>'
            f'<faultcode>SOAP-ENV:Server</faultcode><faultstring>{escape(text)}</faultstring></SOAP-ENV:Fault></SOAP-ENV:Body></SOAP-ENV:Envelope>')


class SimulatedRfc:
    """Answers the RFC collector's function modules (same shapes as pyrfc results)."""

    RUNTIME_ERRORS = ["TIME_OUT", "DBSQL_DUPLICATE_KEY_ERROR", "MESSAGE_TYPE_X", "TSV_TNEW_PAGE_ALLOC_FAILED",
                      "CONVT_NO_NUMBER", "OBJECTS_OBJREF_NOT_ASSIGNED"]

    def __init__(self, seed: str):
        self.seed = seed
        self.p = profile(seed)
        self.r = _rng(f"{seed}:rfc")
        self.sid = sim_sid(seed) if seed.startswith(SIM_HOST + "-") else "SIM"
        self.xbp = False

    def call(self, name: str, **params):
        now = datetime.now(timezone.utc)
        if name == "RFC_SYSTEM_INFO":
            return {"RFCSI_EXPORT": {"RFCSYSID": self.sid, "RFCSAPRL": "758", "RFCDBSYS": "HDB", "RFCHOST": f"sap{self.sid.lower()}app1",
                                     "RFCKERNRL": "793"}}
        if name == "TH_SERVER_LIST":
            return {"LIST": [{"NAME": f"sap{self.sid.lower()}app{i}_{self.sid}_0{i - 1}", "HOST": f"sap{self.sid.lower()}app{i}",
                              "SERV": f"sapdp0{i - 1}", "MSGTYPES": 255} for i in (1, 2)]}
        if name == "/SDF/GET_DUMP_LOG":
            count = self.r.randint(0, 4) + (self.r.randint(5, 25) if self.p["fragile"] else 0)
            rows = []
            for _ in range(count):
                at = now - timedelta(minutes=self.r.randint(0, 23 * 60))
                rows.append({"FIELD1": at.strftime("%Y%m%d"), "FIELD2": at.strftime("%H%M%S"),
                             "FIELD3": self.r.choice(self.RUNTIME_ERRORS), "FIELD4": self.r.choice(["JSMITH", "BATCH_FI", "RFC_PI"]),
                             "FIELD5": f"sap{self.sid.lower()}app1_{self.sid}_00", "FIELD6": "100",
                             "FIELD7": self.r.choice(["SAPMV45A", "ZFI_POSTING", "SAPLBAPT"])})
            return {"ET_E2E_LOG": rows,
                    "ES_E2E_LOG_STRUCT_DESC": {"FIELD1": "Date", "FIELD2": "Time", "FIELD3": "Runtime Error", "FIELD4": "User",
                                               "FIELD5": "Application Server", "FIELD6": "Client", "FIELD7": "Program"}}
        if name == "BAPI_XMI_LOGON":
            self.xbp = True
            return {"SESSIONID": "SIMXMI0001", "RETURN": {"TYPE": "S", "MESSAGE": ""}}
        if name == "BAPI_XMI_LOGOFF":
            self.xbp = False
            return {"RETURN": {"TYPE": "S", "MESSAGE": ""}}
        if name == "BAPI_XBP_JOB_SELECT":
            if not self.xbp:
                return {"SELECTED_JOBS": [], "RETURN": {"TYPE": "E", "MESSAGE": "Not logged on in interface XBP"}}
            jobs = []
            for i in range(self.r.randint(15, 40)):
                start = now - timedelta(minutes=self.r.randint(5, 6 * 24 * 60))
                status = "A" if self.p["fragile"] and self.r.random() < 0.15 else self.r.choice(["F", "F", "F", "F", "R"])
                end = start + timedelta(minutes=self.r.randint(1, 50)) if status in ("F", "A") else None
                jobs.append({"JOBNAME": self.r.choice(["SAP_COLLECTOR_PERFMON", "Z_FI_NIGHTLY", "SAP_REORG_SPOOL", "BTCTRNS1_CHECK",
                                                       "Z_SD_BILLING"]), "JOBCOUNT": f"{10000000 + i:08d}", "STATUS": status,
                             "SDLUNAME": "BATCH_FI", "STRTDATE": start.strftime("%Y%m%d"), "STRTTIME": start.strftime("%H%M%S"),
                             "ENDDATE": end.strftime("%Y%m%d") if end else "", "ENDTIME": end.strftime("%H%M%S") if end else ""})
            return {"SELECTED_JOBS": jobs, "RETURN": {"TYPE": "S", "MESSAGE": ""}}
        from . import CollectorError
        raise CollectorError(f"{name}: function module not found or not remote-enabled")

    def close(self) -> None:
        pass


class SimulatedHana:
    """Answers the HANA collector's named queries with plausible data."""

    def __init__(self, seed: str):
        self.seed = seed
        self.p = profile(seed)
        self.r = _rng(f"{seed}:hana")
        self.now = datetime.now(timezone.utc)

    def query(self, name: str, sql: str, params: tuple = ()) -> list[dict]:
        if is_db_seed(self.seed) and self.seed in _STATE and not all(
                v == "GREEN" for v in _state(self.seed, False)["status"].values()):
            raise ConnectionError("Connection refused: SAP HANA is not running")
        r, p, now = self.r, self.p, self.now
        if name == "ping":
            return [{"OK": 1}]
        if name == "pending_updates":
            return [{"N": 0}]
        if name == "running_jobs":
            return [{"N": r.randint(0, 3)}]
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
        if name == "active_statements":
            return [{"ACTIVE_COUNT": 8 + (12 if p["fragile"] else 0),
                     "LONG_RUNNING": 2 if p["fragile"] else 0,
                     "MAX_DURATION_US": 95000000 if p["fragile"] else 8000000}]
        if name == "service_threads":
            return [{"THREAD_COUNT": 180, "WAITING_THREADS": 32 if p["fragile"] else 8}]
        if name == "expensive_statements":
            return [{"STATEMENT_COUNT": 14 if p["fragile"] else 2,
                     "MAX_DURATION_US": 125000000 if p["fragile"] else 18000000}]
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
        if name == "number_ranges":
            rows = []
            for obj, used in (("RF_BELEG", 0.42), ("RV_BELEG", 0.86 if p["fragile"] else 0.55), ("MATERIALNR", 0.12),
                              ("SPO_NUM", 0.71), ("BU_PARTNER", 0.33), ("EINKBELEG", 0.93 if p["fragile"] and r.random() < 0.5 else 0.6)):
                lo, hi = 1000000000, 1999999999
                rows.append({"CLIENT": "100", "OBJECT": obj, "SUBOBJECT": "", "NRRANGENR": "01", "TOYEAR": "0000",
                             "FROMNUMBER": f"{lo:020d}", "TONUMBER": f"{hi:020d}", "NRLEVEL": f"{int(lo + (hi - lo) * used):020d}"})
            return rows
        if name == "applog_errors":
            return [{"OBJECT": o, "SUBOBJECT": so, "LOGS": r.randint(1, 40) * (4 if p["fragile"] else 1)}
                    for o, so in (("BAL_TEST", ""), ("/SAPAPO/PPM", "ERROR"), ("MDG_BS", "MASS"), ("EDI", "INBOUND"))]
        if name == "batch_input":
            return [] if not p["fragile"] else [{"MANDANT": "100", "GROUPID": "MIGR_VENDORS", "QSTATE": "E",
                                                  "CREDATE": (now - timedelta(days=1)).strftime("%Y%m%d"), "CREATOR": "MIGRATION"}]
        if name == "clients":
            return [{"MANDT": "000", "MTEXT": "SAP AG", "CCCATEGORY": "S", "CCCORACTIV": "2", "CCNOCLIIND": "3"},
                    {"MANDT": "100", "MTEXT": "Production", "CCCATEGORY": "P",
                     "CCCORACTIV": "1" if p["fragile"] else "2", "CCNOCLIIND": "3"}]
        if name == "spool_requests":
            return [{"TOTAL": r.randint(2000, 26000)}]
        if name == "log_disk":
            return [{"USED": 40 * 2**30 + r.randint(0, 20) * 2**30, "TOTAL": 100 * 2**30}]
        if name == "blocked_transactions":
            return [{"BLOCKED": r.randint(0, 3) + (r.randint(4, 12) if p["fragile"] else 0), "MAX_WAIT_S": r.randint(0, 90)}]
        if name == "oom_events":
            return [{"EVENTS": 1 if p["fragile"] and r.random() < 0.3 else 0}]
        if name == "imports":
            return [{"TRKORR": f"S4DK90{1200 + d:04d}", "RETCODE": "0008" if p["fragile"] and d == 0 else "0000",
                     "TRTIME": (now - timedelta(days=d, hours=3)).strftime("%Y%m%d%H%M%S")} for d in range(4)]
        return []

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------------------- SSH (OS layer)
def ssh_host_key(host: str) -> str:
    import base64
    import hashlib
    return "SHA256:" + base64.b64encode(hashlib.sha256(f"sim-hostkey:{host}".encode()).digest()).decode().rstrip("=")


def _sim_os(host: str, sid: str, db: bool) -> dict:
    r = _rng(f"{host}:os")
    redhat = sim_kind(sid) == "ecc"
    return {
        "name": "Red Hat Enterprise Linux" if redhat else "SLES",
        "version": "8.8 (Ootpa)" if redhat else "15-SP5",
        "pretty": "Red Hat Enterprise Linux 8.8 (Ootpa)" if redhat else "SUSE Linux Enterprise Server 15 SP5",
        "id": "rhel" if redhat else "sles",
        "kernel": "4.18.0-477.27.1.el8_8.x86_64" if redhat else "5.14.21-150500.55.49-default",
        "cpus": 64 if db else r.choice([8, 16, 32]),
        "mem_kb": (1024 if db else r.choice([64, 128, 256])) * 1024 * 1024,
        "avail_ratio": r.uniform(0.15, 0.6),
        "virt": r.choice(["kvm", "vmware", "none"]) if not db else "none",
    }


def ssh_command(host: str, user: str, command: str) -> str:
    db = host.endswith("-db")
    sid = sim_sid(host)
    kind = sim_kind(sid)
    hana_sid = f"{sid[:2]}H"
    o = _sim_os(host, sid, db)
    short = (f"hdb{sid.lower()}" if db else f"{sid.lower()}app") if kind != "ecc" or not db else f"db{sid.lower()}"
    if "@@os-release" in command:
        lines = ["@@os-release", f'NAME="{o["name"]}"', f'VERSION="{o["version"]}"', f'ID="{o["id"]}"', f'PRETTY_NAME="{o["pretty"]}"',
                 "@@uname", f"Linux {o['kernel']} x86_64",
                 "@@hostname", short, f"{short}.corp.example.test",
                 "@@ip", f"10.20.{_rng(host).randint(1, 250)}.{_rng(host + 'x').randint(2, 250)}",
                 "@@cpu", str(o["cpus"]), "model name\t: Intel(R) Xeon(R) Platinum 8380 CPU @ 2.30GHz",
                 "@@mem", f"MemTotal:       {o['mem_kb']} kB", f"MemAvailable:   {int(o['mem_kb'] * o['avail_ratio'])} kB",
                 f"SwapTotal:      {8 * 1024 * 1024} kB", "SwapFree:       8000000 kB",
                 "@@load", f"{_rng(host + 'l').uniform(0.5, 6):.2f} 1.10 0.95 2/1450 31337",
                 "@@cpustat", "cpu  1000000 2000 300000 8000000 50000 0 10000 0 0 0",
                 f"cpu  {1000000 + (busy := _rng(host + str(int(time.time()) // 60)).randint(15, 85))} 2000 300000 {8000000 + (100 - busy)} 50000 0 10000 0 0 0",
                 "@@uptime", f"{_rng(host + 'u').randint(5, 140) * 86400 + 4711}.33 1000.00",
                 "@@virt", o["virt"], "@@tz", "UTC+0000",
                 "@@df", "Filesystem 1024-blocks Used Available Capacity Mounted on",
                 "/dev/sda2 104857600 41943040 62914560 40% /"]
        if db and kind != "ecc":
            lines += ["/dev/sdb1 4294967296 2920577761 1374389535 68% /hana/data",
                      "/dev/sdc1 1073741824 322122547 751619277 30% /hana/log",
                      "/dev/sdd1 1073741824 429496730 644245094 40% /hana/shared"]
        elif db:
            lines += ["/dev/sdb1 3221225472 2254857830 966367642 70% /oracle"]
        else:
            lines += [f"/dev/sdb1 209715200 {int(209715200 * 0.45)} {int(209715200 * 0.55)} 45% /usr/sap",
                      "nfs:/sapmnt 104857600 31457280 73400320 30% /sapmnt"]
        lines += ["@@whoami", user, "@@sapservices"]
        if db and kind != "ecc":
            lines.append(f"systemctl --no-ask-password start SAP{hana_sid}_00 # sapstartsrv pf=/usr/sap/{hana_sid}/SYS/profile/{hana_sid}_HDB00_hdb{sid.lower()}")
        elif not db:
            java = kind == "java"
            for row in _layout(host, java):
                lines.append(f"LD_LIBRARY_PATH=/usr/sap/{sid}/{row['name']}/exe; export LD_LIBRARY_PATH; "
                             f"/usr/sap/{sid}/{row['name']}/exe/sapstartsrv pf=/usr/sap/{sid}/SYS/profile/{sid}_{row['name']}_{row['hostname']} -D -u {sid.lower()}adm")
            lines.append("/usr/sap/DAA/SMDA98/exe/sapstartsrv pf=/usr/sap/DAA/SYS/profile/DAA_SMDA98_" + short + " -D -u daaadm")
        lines.append("@@procs")
        if db and kind != "ecc":
            if db_running(host[: -len("-db")]) is not False and all(v == "GREEN" for v in _state(host, False)["status"].values()):
                lines += ["hdbdaemon", "hdbindexserver", "hdbnameserver", "sapstartsrv"]
            else:
                lines += ["sapstartsrv"]
        elif db:
            lines += ["ora_pmon_" + sid, "ora_smon_" + sid, "saphostexec"]
        else:
            lines += ["disp+work", "enq_server", "gwrd", "icman", "msg_server", "sapstartsrv", "saphostexec"]
        lines.append("@@usrsap")
        lines.append("@@dbdirs")
        if db and kind == "ecc":
            lines.append(f"/oracle/{sid}/19")
        lines.append("@@end")
        return "\n".join(lines) + "\n"
    if "DEFAULT.PFL" in command:
        if f"/{sid}/" not in command:
            return ""
        dbms = DBMS.get(kind, "hdb")
        return "\n".join([f"SAPSYSTEMNAME = {sid}", f"SAPGLOBALHOST = sap{'scs' if kind == 'java' else 'ascs'}",
                          f"SAPDBHOST = {db_host_for(sid)}", f"dbms/type = {dbms}", "rdisp/mshost = sapascs",
                          f"dbs/hdb/dbname = {sid}" if dbms == "hdb" else f"dbs/ora/tnsname = {sid}",
                          "dbs/hdb/schema = SAPHANADB" if dbms == "hdb" else f"dbs/ora/schema = SAP{sid}"]) + "\n"
    if "manifest" in command:
        return "release: 2.00.079.00\nfullversion: 2.00.079.00.1720500000\nbuilt: 2024-07-09\n"
    if "saphostctrl" in command:
        if db and kind != "ecc":
            return f"Instance name: HDB00, Database name: {sid}, Database type: hdb, Database status: Running\n"
        if db:
            return f"Instance name: {sid}, Database name: {sid}, Database type: ora, Database status: Running\n"
        return ""
    return ""
