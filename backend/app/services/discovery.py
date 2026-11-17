"""Auto-discovery of SAP systems through the SAP Start Service (sapstartsrv).

Given a host (name or IP) and optionally <sid>adm credentials, discovery:

1. probes the sapstartsrv ports 5<NN>13 (HTTP) / 5<NN>14 (HTTPS) for every instance number 00-99
   (or only the one supplied), in parallel, after the host passed the outbound policy;
2. asks the first responding instance for GetInstanceProperties (SID, instance name) and
   GetSystemInstanceList (every instance of the system: ASCS/SCS, ERS, PAS, AAS, Java, Web Dispatcher ...);
3. classifies each instance's role from its instance name (ASCS01, ERS02, DVEBMGS00, D10, J00, W00 ...)
   with the sapstartsrv feature list as fallback;
4. detects the database (profile parameter dbms/type) and the product and release from the ABAP
   software components (S4CORE, SAP_APPL, DW4CORE ...), both best effort;
5. checks whether the credentials may start/stop instances (AccessCheck, best effort).

``register`` turns a discovery into a registered system, instance inventory and sapcontrol collector
(plus an optional HANA collector) and runs the first collection, so monitoring starts immediately.
"""
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy import select, update

from .. import config
from ..db import collectors, get_db, sap_instances, sap_systems
from . import systems
from .collectors import CollectorError, runner, sapcontrol, simulator
from .target_policy import TargetPolicyError, resolve_approved_addresses

ROLES = ("ASCS", "SCS", "ERS", "PAS", "AAS", "JAVA", "HANA", "WEBDISP", "SMDA", "GATEWAY", "OTHER")
ROLE_LABELS = {"ASCS": "ABAP central services", "SCS": "Java central services", "ERS": "Enqueue replication",
               "PAS": "Primary application server", "AAS": "Additional application server", "JAVA": "Java application server",
               "HANA": "HANA database instance", "WEBDISP": "Web Dispatcher", "SMDA": "Diagnostics agent",
               "GATEWAY": "Standalone gateway", "OTHER": "Other"}
NAME_PREFIXES = (("ASCS", "ASCS"), ("SCS", "SCS"), ("ERS", "ERS"), ("DVEBMGS", "PAS"), ("SMDA", "SMDA"), ("HDB", "HANA"),
                 ("D", "AAS"), ("J", "JAVA"), ("W", "WEBDISP"), ("G", "GATEWAY"))
DBMS_TYPES = {"hdb": "HANA", "ora": "ORACLE", "db6": "DB2", "db4": "DB4", "db2": "DB6Z", "mss": "MSSQL",
              "ada": "MAXDB", "syb": "ASE", "sap": "MAXDB"}
S4_RELEASES = {"100": "1511", "101": "1610", "102": "1709", "103": "1809", "104": "1909", "105": "2020",
               "106": "2021", "107": "2022", "108": "2023", "109": "2025"}
ECC_EHP = {"600": "ECC 6.0", "602": "EHP2", "603": "EHP3", "604": "EHP4", "605": "EHP5", "606": "EHP6",
           "616": "EHP6 for HANA", "617": "EHP7", "618": "EHP8"}
# Checked in order: the first component found decides the product.
PRODUCT_COMPONENTS = (("S4CORE", "S4HANA"), ("DW4CORE", "BW4HANA"), ("BBPCRM", "CRM"), ("SRM_SERVER", "SRM"),
                      ("SCM", "SCM"), ("ST", "SOLMAN"), ("GRCFND_A", "GRC"), ("SAP_APPL", "ECC"), ("BI_CONT", "BW"),
                      ("SAP_GWFND", "GATEWAY"))
ENV_BY_SUFFIX = {"P": "PRD", "Q": "QAS", "T": "QAS", "D": "DEV", "S": "SBX", "X": "SBX", "R": "PRE"}


class DiscoveryError(ValueError):
    pass


# ----------------------------------------------------------------------------- probing
def _transport(host: str):
    if simulator.is_sim(host):
        if not config.SIMULATOR_ENABLED:
            raise DiscoveryError("The demo simulator is disabled (SIMULATOR_ENABLED=false)")
        return simulator.sapcontrol_transport(simulator.seed_for(host, host))
    return sapcontrol.http_transport


def _tcp_open(address: str, port: int) -> bool:
    try:
        with socket.create_connection((address, port), timeout=config.DISCOVERY_PROBE_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


def probe(host: str, use_tls: bool, instance_nr: str | None = None) -> list[str]:
    """Instance numbers whose sapstartsrv port answers on the host."""
    numbers = [instance_nr.zfill(2)] if instance_nr else [f"{n:02d}" for n in range(100)]
    port_of = (lambda nr: int(f"5{nr}14")) if use_tls else (lambda nr: int(f"5{nr}13"))
    if simulator.is_sim(host):
        transport = _transport(host)

        def sim_open(nr):
            try:
                transport(sapcontrol.Endpoint(host, port_of(nr), use_tls, False, None, None), "GetInstanceProperties", "")
                return True
            except Exception:  # noqa: BLE001 - closed port
                return False
        return [nr for nr in numbers if sim_open(nr)]
    try:
        addresses = resolve_approved_addresses(host, port_of(numbers[0]), check_port=False)
    except TargetPolicyError as exc:
        raise DiscoveryError(f"Blocked by outbound policy: {exc}") from exc
    address = addresses[0]
    with ThreadPoolExecutor(max_workers=max(1, config.DISCOVERY_MAX_PARALLEL)) as pool:
        results = list(pool.map(lambda nr: (nr, _tcp_open(address, port_of(nr))), numbers))
    return [nr for nr, ok in results if ok]


# ----------------------------------------------------------------------------- classification
def classify_role(instance_name: str | None, features: str, java_only: bool = False) -> tuple[str, str]:
    """(role, source) from the instance name, falling back to sapstartsrv features."""
    name = (instance_name or "").upper()
    match = re.match(r"^([A-Z]+)(\d{2})$", name)
    if match:
        prefix = match.group(1)
        for candidate, role in NAME_PREFIXES:
            if prefix == candidate:
                return role, "instance_name"
    feats = set(f.strip().upper() for f in (features or "").split("|") if f.strip())
    if "ENQREP" in feats:
        return "ERS", "features"
    if feats & {"MESSAGESERVER", "ENQUE"}:
        return ("SCS" if java_only else "ASCS"), "features"
    if "HDB" in feats:
        return "HANA", "features"
    if "ABAP" in feats:
        return "AAS", "features"
    if "J2EE" in feats:
        return "JAVA", "features"
    if "WEBDISP" in feats:
        return "WEBDISP", "features"
    if "SMDAGENT" in feats:
        return "SMDA", "features"
    if "GATEWAY" in feats:
        return "GATEWAY", "features"
    return "OTHER", "features"


def assign_roles(instances: list[dict]) -> list[dict]:
    """Classify every instance; without a DVEBMGS instance the lowest-numbered ABAP dialog instance is the PAS."""
    java_only = not any("ABAP" in (i.get("features") or "").upper() for i in instances)
    for inst in instances:
        inst["role"], inst["role_source"] = classify_role(inst.get("instance_name"), inst.get("features", ""), java_only)
    if not any(i["role"] == "PAS" for i in instances):
        dialog = sorted((i for i in instances if i["role"] == "AAS"), key=lambda i: (i["instance_nr"], i["hostname"]))
        if dialog:
            dialog[0]["role"], dialog[0]["role_source"] = "PAS", "inferred"
    return instances


def _release(raw: str) -> str:
    raw = (raw or "").strip()
    return f"{raw[0]}.{raw[1:]}" if len(raw) == 3 and raw[0].isdigit() else raw


def detect_product(components: list[dict], stack: str) -> dict:
    by_name = {str(c.get("component", "")).upper(): c for c in components}
    basis = by_name.get("SAP_BASIS")
    result = {"product": "NW_JAVA" if stack == "JAVA" else "NW_ABAP", "product_version": None,
              "basis_release": _release(basis.get("release")) if basis else None, "evidence": None}
    for component, product in PRODUCT_COMPONENTS:
        if component in by_name:
            rel = str(by_name[component].get("release", "")).strip()
            result["product"], result["evidence"] = product, f"{component} {rel}"
            if product == "S4HANA":
                result["product_version"] = S4_RELEASES.get(rel, rel)
            elif product == "ECC":
                result["product_version"] = ECC_EHP.get(rel, rel)
            elif product == "BW4HANA":
                result["product_version"] = {"100": "1.0", "200": "2.0"}.get(rel, rel)
            else:
                result["product_version"] = rel or None
            break
    return result


def suggest_environment(sid: str) -> str:
    return ENV_BY_SUFFIX.get(sid[-1:].upper(), "OTHER")


# ----------------------------------------------------------------------------- scan
def _props(client, endpoint) -> dict:
    items = client.call("GetInstanceProperties", endpoint=endpoint)
    return {i.get("property"): i.get("value") for i in items if i.get("property")}


def scan(host: str, username: str | None = None, password: str | None = None, use_tls: bool = False,
         verify_tls: bool = True, instance_nr: str | None = None) -> dict:
    host = (host or "").strip().lower()
    if not host:
        raise DiscoveryError("Enter a hostname or IP address")
    if instance_nr is not None and not re.fullmatch(r"\d{1,2}", str(instance_nr)):
        raise DiscoveryError("Instance number must be 00-99")
    warnings: list[str] = []
    open_nrs = probe(host, use_tls, instance_nr)
    if not open_nrs:
        raise DiscoveryError(f"No SAP Start Service answered on {host} "
                             f"(ports 5<NN>{'14' if use_tls else '13'}). Check the host, firewall and TLS setting.")
    transport = _transport(host)
    port = int(f"5{open_nrs[0]}{'14' if use_tls else '13'}")
    endpoint = sapcontrol.Endpoint(host, port, use_tls, verify_tls, username or None, password or None)
    client = sapcontrol.SapControlClient(endpoint, transport)
    try:
        first_props = _props(client, endpoint)
        raw = client.call("GetSystemInstanceList")
    except CollectorError as exc:
        raise DiscoveryError(f"sapstartsrv on {host}:{port} answered but discovery failed: {exc}") from exc
    sid = (first_props.get("SAPSYSTEMNAME") or "").upper()
    if not raw:
        raise DiscoveryError("GetSystemInstanceList returned no instances")

    instances = []
    for item in raw:
        nr = str(item.get("instanceNr", "")).zfill(2)
        inst = {"hostname": (item.get("hostname") or "").lower(), "instance_nr": nr, "instance_name": None,
                "features": item.get("features", ""), "http_port": int(item.get("httpPort") or 0) or None,
                "https_port": int(item.get("httpsPort") or 0) or None, "start_priority": item.get("startPriority"),
                "status": (item.get("dispstatus") or "").replace("SAPControl-", "") or "GRAY", "reachable": True}
        try:
            props = _props(client, client.instance_endpoint(item))
            inst["instance_name"] = props.get("INSTANCE_NAME")
            sid = sid or (props.get("SAPSYSTEMNAME") or "").upper()
        except CollectorError as exc:
            inst["reachable"] = False
            warnings.append(f"{inst['hostname']} instance {nr}: {exc}")
        instances.append(inst)
    if not sid or not systems.SID_PATTERN.match(sid):
        raise DiscoveryError("Could not read the SAP system ID (SAPSYSTEMNAME) from sapstartsrv")
    assign_roles(instances)
    instances.sort(key=lambda i: (ROLES.index(i["role"]), i["instance_nr"]))

    feats = "|".join(i["features"] for i in instances).upper()
    has_abap, has_java = "ABAP" in feats, "J2EE" in feats
    stack = "DUAL" if has_abap and has_java else ("JAVA" if has_java else "ABAP")

    abap = next((i for i in instances if i["role"] in ("PAS", "AAS") and i["status"] == "GREEN"), None)
    probe_item = next(({"hostname": i["hostname"], "instanceNr": i["instance_nr"], "httpPort": i["http_port"],
                        "httpsPort": i["https_port"]} for i in instances if i is abap), None)
    ep = client.instance_endpoint(probe_item) if probe_item else endpoint

    database, db_host = None, None
    try:
        dbms = (client.call_value("ParameterValue", "<parameter>dbms/type</parameter>", ep) or "").lower()
        database = DBMS_TYPES.get(dbms)
        db_host = client.call_value("ParameterValue", "<parameter>SAPDBHOST</parameter>", ep) or None
    except CollectorError as exc:
        warnings.append(f"Database type not readable (ParameterValue): {exc}")
    if database is None and any(i["role"] == "HANA" for i in instances):
        database = "HANA"

    components: list[dict] = []
    if abap:
        try:
            components = client.call("ABAPGetComponentList", endpoint=ep)
        except CollectorError as exc:
            warnings.append(f"Software components not readable (ABAPGetComponentList): {exc}")
    elif has_abap:
        warnings.append("No running ABAP instance: product and release could not be read; choose them below.")
    product = detect_product(components, stack)
    if product["product"] in ("S4HANA", "BW4HANA") and database is None:
        database = "HANA"

    control_access = None
    if username:
        try:
            client.call("AccessCheck", "<function>Start</function>")
            control_access = True
        except CollectorError as exc:
            text = str(exc).lower()
            if "permission" in text or "denied" in text or "401" in text or "authentication" in text:
                control_access = False
                warnings.append("These credentials may not start/stop instances (use <sid>adm or a user in service/admin_users).")
            else:
                warnings.append(f"Control permission could not be verified: {exc}")
    else:
        warnings.append("No OS credentials given: protected monitoring methods and start/stop will not be available.")

    with get_db() as connection:
        existing = [dict(r) for r in connection.execute(
            select(sap_systems.c.id, sap_systems.c.sid, sap_systems.c.landscape, sap_systems.c.name)
            .where(sap_systems.c.sid == sid)).mappings().all()]
    return {
        "host": host, "sid": sid, "instances": instances, "stack": stack, "database": database, "db_host": db_host,
        "product": product["product"], "product_version": product["product_version"],
        "basis_release": product["basis_release"], "product_evidence": product["evidence"],
        "components": [{"component": c.get("component"), "release": c.get("release"), "patchlevel": c.get("patchlevel")}
                       for c in components][:60],
        "suggested_environment": suggest_environment(sid), "control_access": control_access,
        "collector": {"host": host, "port": port, "use_tls": use_tls, "verify_tls": verify_tls,
                      "username": username or None},
        "existing_systems": existing, "warnings": warnings, "scanned_at": datetime.now(timezone.utc).isoformat(),
    }


# ----------------------------------------------------------------------------- inventory
def _role_view(row) -> dict:
    item = dict(row)
    item["role_label"] = ROLE_LABELS.get(item["role"], item["role"])
    if hasattr(item.get("last_seen_at"), "isoformat"):
        item["last_seen_at"] = item["last_seen_at"].isoformat()
    return item


def list_instances(system_id: int) -> list[dict]:
    with get_db() as connection:
        rows = connection.execute(select(sap_instances).where(sap_instances.c.system_id == system_id)).mappings().all()
    items = [_role_view(r) for r in rows]
    items.sort(key=lambda i: (ROLES.index(i["role"]) if i["role"] in ROLES else 99, i["instance_nr"], i["hostname"]))
    return items


def upsert_instances(system_id: int, instances: list[dict], overrides: dict | None = None) -> None:
    """Store discovered instances. Manually assigned roles survive re-discovery unless overridden here."""
    overrides = overrides or {}
    now = datetime.now(timezone.utc)
    with get_db() as connection:
        current = {(r["hostname"], r["instance_nr"]): dict(r) for r in connection.execute(
            select(sap_instances).where(sap_instances.c.system_id == system_id)).mappings().all()}
        for inst in instances:
            key = (inst["hostname"], inst["instance_nr"])
            values = {k: inst.get(k) for k in ("instance_name", "features", "http_port", "https_port", "start_priority", "status")}
            values["last_seen_at"] = now
            override = overrides.get(f"{inst['hostname']}:{inst['instance_nr']}")
            if override:
                if override not in ROLES:
                    raise DiscoveryError(f"Unknown role {override}")
                values.update(role=override, role_source="manual")
            if key in current:
                if not override and current[key]["role_source"] != "manual":
                    values.update(role=inst["role"], role_source=inst["role_source"])
                connection.execute(update(sap_instances).where(sap_instances.c.id == current[key]["id"]).values(**values))
            else:
                values.setdefault("role", inst["role"])
                values.setdefault("role_source", inst["role_source"])
                connection.execute(sap_instances.insert().values(system_id=system_id, hostname=inst["hostname"],
                                                                 instance_nr=inst["instance_nr"], **values))


def sync_from_collection(system_id: int, raw_instances: list[dict]) -> None:
    """Keep the inventory current from each sapcontrol collection (new instances get a features-based role)."""
    with get_db() as connection:
        known = {(r["hostname"], r["instance_nr"]): dict(r) for r in connection.execute(
            select(sap_instances).where(sap_instances.c.system_id == system_id)).mappings().all()}
    rows = []
    for inst in raw_instances:
        key = ((inst.get("hostname") or "").lower(), inst.get("instance_nr"))
        prev = known.get(key)
        row = {"hostname": key[0], "instance_nr": key[1], "features": inst.get("features", ""),
               "status": (inst.get("status") or "").upper().replace("SAPCONTROL-", "") or "GRAY",
               "instance_name": prev["instance_name"] if prev else None,
               "http_port": prev["http_port"] if prev else None, "https_port": prev["https_port"] if prev else None,
               "start_priority": prev["start_priority"] if prev else None}
        rows.append(row)
    assign_roles(rows)
    for row in rows:
        prev = known.get((row["hostname"], row["instance_nr"]))
        if prev and prev["role_source"] in ("manual", "instance_name", "inferred"):
            row["role"], row["role_source"] = prev["role"], prev["role_source"]
    upsert_instances(system_id, rows)


def set_role(system_id: int, instance_id: int, role: str) -> dict:
    role = (role or "").upper()
    if role not in ROLES:
        raise DiscoveryError(f"role must be one of {', '.join(ROLES)}")
    with get_db() as connection:
        result = connection.execute(update(sap_instances).where(sap_instances.c.id == instance_id,
                                                                sap_instances.c.system_id == system_id)
                                    .values(role=role, role_source="manual"))
        if result.rowcount == 0:
            raise LookupError("Instance not found")
        row = connection.execute(select(sap_instances).where(sap_instances.c.id == instance_id)).mappings().one()
    return _role_view(row)


def refresh(system_id: int) -> list[dict]:
    """Re-read the instance list through the system's sapcontrol collector."""
    with get_db() as connection:
        row = connection.execute(select(collectors).where(collectors.c.system_id == system_id,
                                                          collectors.c.type == "sapcontrol")
                                 .order_by(collectors.c.id)).mappings().first()
    if row is None:
        raise DiscoveryError("This system has no sapcontrol collector; run discovery or add one under Data collection")
    client = control_client(dict(row))
    try:
        raw = client.call("GetSystemInstanceList")
    except CollectorError as exc:
        raise DiscoveryError(str(exc)) from exc
    instances = []
    for item in raw:
        inst = {"hostname": (item.get("hostname") or "").lower(), "instance_nr": str(item.get("instanceNr", "")).zfill(2),
                "features": item.get("features", ""), "http_port": int(item.get("httpPort") or 0) or None,
                "https_port": int(item.get("httpsPort") or 0) or None, "start_priority": item.get("startPriority"),
                "status": (item.get("dispstatus") or "").replace("SAPControl-", "") or "GRAY", "instance_name": None}
        try:
            inst["instance_name"] = _props(client, client.instance_endpoint(item)).get("INSTANCE_NAME")
        except CollectorError:
            pass
        instances.append(inst)
    assign_roles(instances)
    upsert_instances(system_id, instances)
    return list_instances(system_id)


def control_client(collector_row: dict) -> sapcontrol.SapControlClient:
    from . import vault
    endpoint = sapcontrol.Endpoint(collector_row["host"], collector_row["port"], bool(collector_row["use_tls"]),
                                   bool(collector_row["verify_tls"]), collector_row["username"],
                                   vault.decrypt(collector_row["password_enc"]))
    if simulator.is_sim(collector_row["host"]):
        with get_db() as connection:
            system = dict(connection.execute(select(sap_systems).where(sap_systems.c.id == collector_row["system_id"]))
                          .mappings().one())
        transport = simulator.sapcontrol_transport(simulator.seed_for(collector_row["host"], runner._seed(system)),
                                                   java=system["stack"] == "JAVA")
    else:
        transport = sapcontrol.http_transport
    return sapcontrol.SapControlClient(endpoint, transport)


# ----------------------------------------------------------------------------- register
def register(data: dict) -> dict:
    """Scan again (never trust a client-side instance list), create or update the system, store the inventory,
    create collectors and run the first collection."""
    found = scan(data["host"], data.get("username"), data.get("password"), bool(data.get("use_tls")),
                 data.get("verify_tls", True), data.get("instance_nr"))
    if data.get("expected_sid") and data["expected_sid"].upper() != found["sid"]:
        raise DiscoveryError(f"The host now reports SID {found['sid']}, not {data['expected_sid'].upper()}; scan again")
    fields = {
        "sid": found["sid"], "name": data.get("name") or f"{found['sid']} ({found['host']})",
        "product": data.get("product") or found["product"], "product_version": data.get("product_version") or found["product_version"],
        "basis_release": data.get("basis_release") or found["basis_release"], "stack": data.get("stack") or found["stack"],
        "database": data.get("database") or found["database"], "environment": data.get("environment") or found["suggested_environment"],
        "landscape": data.get("landscape") or "default", "host": found["host"], "description": data.get("description"),
        "default_client": data.get("default_client"), "timezone": data.get("timezone"),
    }
    if not fields["database"]:
        raise DiscoveryError("The database type could not be detected; choose it before registering")
    if data.get("control_enabled") is not None:
        fields["control_enabled"] = bool(data["control_enabled"])
    try:
        if data.get("system_id"):
            existing = systems.get_system(int(data["system_id"]))
            if existing["sid"] != found["sid"]:
                raise DiscoveryError(f"System {data['system_id']} is {existing['sid']}, but the host reports {found['sid']}")
            fields = {k: v for k, v in fields.items() if v is not None and k not in ("sid",)}
            system = systems.update_system(existing["id"], fields)
            created = False
        else:
            system = systems.create_system({k: v for k, v in fields.items() if v is not None})
            created = True
    except (systems.SystemValidationError, systems.SystemConflict) as exc:
        raise DiscoveryError(str(exc)) from exc

    upsert_instances(system["id"], found["instances"], data.get("role_overrides") or {})

    collector_cfg = {"type": "sapcontrol", "host": found["collector"]["host"], "port": found["collector"]["port"],
                     "use_tls": found["collector"]["use_tls"], "verify_tls": found["collector"]["verify_tls"],
                     "username": data.get("username") or None, "password": data.get("password") or None,
                     "interval_seconds": int(data.get("interval_seconds") or 300)}
    with get_db() as connection:
        current = connection.execute(select(collectors).where(collectors.c.system_id == system["id"],
                                                              collectors.c.type == "sapcontrol")).mappings().first()
    try:
        if current:
            changes = {k: v for k, v in collector_cfg.items() if k not in ("type", "password")}
            if collector_cfg["password"]:
                changes["password"] = collector_cfg["password"]
            sc = runner.update_collector(current["id"], changes)
        else:
            sc = runner.create(system["id"], collector_cfg)
    except CollectorError as exc:
        raise DiscoveryError(f"Collector: {exc}") from exc
    created_collectors = [sc]
    hana_cfg = data.get("hana")
    if hana_cfg and hana_cfg.get("host"):
        try:
            created_collectors.append(runner.create(system["id"], {
                "type": "hana", "host": hana_cfg["host"], "port": int(hana_cfg.get("port") or 30015),
                "use_tls": bool(hana_cfg.get("use_tls", True)), "verify_tls": bool(hana_cfg.get("verify_tls", True)),
                "username": hana_cfg.get("username"), "password": hana_cfg.get("password"),
                "database_name": hana_cfg.get("database_name"), "abap_schema": hana_cfg.get("abap_schema"),
                "interval_seconds": int(data.get("interval_seconds") or 300)}))
        except CollectorError as exc:
            raise DiscoveryError(f"HANA collector: {exc}") from exc
    first_runs = [runner.run(c["id"]) for c in created_collectors] if data.get("start_monitoring", True) else []
    return {"system": systems.get_system(system["id"]), "created": created, "instances": list_instances(system["id"]),
            "collectors": [runner.view(runner.get(c["id"])) for c in created_collectors],
            "first_runs": [{"collector_id": r["collector_id"], "status": r["status"], "error": r["error"]} for r in first_runs],
            "warnings": found["warnings"]}
