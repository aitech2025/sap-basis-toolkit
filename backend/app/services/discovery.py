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
from ..db import collectors, get_db, sap_instances, sap_systems, topology_assets, topology_links
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

    hana_instance = None
    if database == "HANA" and db_host:
        try:
            hana_instance = detect_hana(db_host.lower(), use_tls, verify_tls)
        except DiscoveryError as exc:
            warnings.append(f"HANA instance not found for start/stop ({exc}); you can add it later as a "
                            "'HANA instance (sapcontrol)' collector.")

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

    existing = _existing_systems(sid)
    return {
        "host": host, "sid": sid, "instances": instances, "stack": stack, "database": database, "db_host": db_host,
        "product": product["product"], "product_version": product["product_version"],
        "basis_release": product["basis_release"], "product_evidence": product["evidence"],
        "components": [{"component": c.get("component"), "release": c.get("release"), "patchlevel": c.get("patchlevel")}
                       for c in components][:60],
        "suggested_environment": suggest_environment(sid), "control_access": control_access,
        "collector": {"host": host, "port": port, "use_tls": use_tls, "verify_tls": verify_tls,
                      "username": username or None},
        "hana_instance": hana_instance, "existing_systems": existing, "warnings": warnings, "scanned_at": datetime.now(timezone.utc).isoformat(),
    }


def _existing_systems(sid: str) -> list[dict]:
    """Registered systems with this SID (any landscape), with their layer selection so re-discovery can keep it."""
    with get_db() as connection:
        rows = connection.execute(select(sap_systems.c.id, sap_systems.c.sid, sap_systems.c.landscape, sap_systems.c.name,
                                         sap_systems.c.monitoring_layers).where(sap_systems.c.sid == sid)).mappings().all()
    return [{**dict(r), "monitoring_layers": [l for l in systems.LAYERS if l in systems.layers_of(r)]} for r in rows]


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


def sync_from_collection(system_id: int, raw_instances: list[dict], force_role: str | None = None) -> None:
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
    if force_role:
        for row in rows:
            row["role"], row["role_source"] = force_role, "features"
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
    with get_db() as connection:
        system = dict(connection.execute(select(sap_systems).where(sap_systems.c.id == collector_row["system_id"]))
                      .mappings().one())
    return runner.sapcontrol_client(collector_row, system)


def detect_hana(db_host: str, use_tls: bool, verify_tls: bool) -> dict:
    """Find the HANA instance's own sapstartsrv on the database host (HDB<NN>)."""
    numbers = probe(db_host, use_tls)
    transport = _transport(db_host)
    for nr in numbers:
        port = int(f"5{nr}{'14' if use_tls else '13'}")
        client = sapcontrol.SapControlClient(sapcontrol.Endpoint(db_host, port, use_tls, verify_tls, None, None), transport)
        try:
            props = _props(client, client.endpoint)
        except CollectorError:
            continue
        if not (props.get("INSTANCE_NAME") or "").upper().startswith("HDB"):
            continue
        try:
            raw = client.call("GetSystemInstanceList")
        except CollectorError:
            raw = [{"hostname": props.get("SAPLOCALHOST") or db_host, "instanceNr": nr, "features": "HDB",
                    "dispstatus": "SAPControl-GRAY"}]
        instances = [{"hostname": (i.get("hostname") or "").lower(), "instance_nr": str(i.get("instanceNr", "")).zfill(2),
                      "instance_name": f"HDB{str(i.get('instanceNr', '')).zfill(2)}", "role": "HANA", "role_source": "features",
                      "features": i.get("features", ""), "http_port": int(i.get("httpPort") or 0) or None,
                      "https_port": int(i.get("httpsPort") or 0) or None, "start_priority": i.get("startPriority"),
                      "status": (i.get("dispstatus") or "").replace("SAPControl-", "") or "GRAY"} for i in raw]
        hana_sid = (props.get("SAPSYSTEMNAME") or "").upper()
        return {"host": db_host, "port": port, "instance_nr": nr, "sid": hana_sid, "use_tls": use_tls,
                "verify_tls": verify_tls, "suggested_user": f"{hana_sid.lower()}adm" if hana_sid else None,
                "instances": instances}
    raise DiscoveryError(f"No HANA sapstartsrv (HDB<NN>) answered on {db_host}")


# ----------------------------------------------------------------------------- register
def _requested_layers(data: dict) -> tuple[str | None, set[str]]:
    """(value to store, effective layers). Without a choice, new systems get every layer and re-discovery keeps the
    existing selection."""
    try:
        value = systems.normalise_layers(data.get("monitoring_layers"))
    except systems.SystemValidationError as exc:
        raise DiscoveryError(str(exc)) from exc
    if value is not None:
        return value, systems.layers_of({"monitoring_layers": value})
    if data.get("system_id"):
        return None, systems.layers_of(systems.get_system(int(data["system_id"])))
    return None, set(systems.LAYERS)


def register(data: dict) -> dict:
    """Scan again (never trust a client-side instance list), create or update the system, store the inventory,
    create collectors for the selected monitoring layers and run the first collection."""
    os_method = data.get("method") == "os"
    layer_value, layers = _requested_layers(data)
    db_target = None
    if os_method:
        found, os_target, db_target, (sap_user, sap_password) = _os_scan(data)
        data = {**data, "username": sap_user, "password": sap_password}
        if not found.get("collector") and "application" in layers:
            raise DiscoveryError("The SAP Start Service of this system is not reachable; start it or open port 5<NN>13 and scan "
                                 "again, or register without the SAP application layer.")
        if data.get("hana") and not data["hana"].get("abap_schema") and (found.get("database_layer") or {}).get("schema"):
            data["hana"] = {**data["hana"], "abap_schema": found["database_layer"]["schema"]}
    else:
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
    fields["monitoring_layers"] = layer_value
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

    created_collectors = []
    if "application" in layers:
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
                created_collectors.append(runner.update_collector(current["id"], changes))
            else:
                created_collectors.append(runner.create(system["id"], collector_cfg))
        except CollectorError as exc:
            raise DiscoveryError(f"Collector: {exc}") from exc
    else:
        found["warnings"].append("The SAP application layer is not selected: no SAP Start Service collector was set up, "
                                 "so availability is not monitored and start/stop from Sentinel is not available.")
    db_ctl = data.get("db_control")
    if "database" not in layers and (db_ctl or (data.get("hana") or {}).get("host")):
        found["warnings"].append("HANA connections were not set up because the database layer is not selected.")
        db_ctl, data = None, {**data, "hana": None}
    if db_ctl and found.get("hana_instance"):
        hi = found["hana_instance"]
        upsert_instances(system["id"], hi["instances"])
        with get_db() as connection:
            existing_db = connection.execute(select(collectors).where(collectors.c.system_id == system["id"],
                                                                      collectors.c.type == "hana_sapcontrol")).mappings().first()
        cfg = {"host": hi["host"], "port": hi["port"], "use_tls": hi["use_tls"], "verify_tls": hi["verify_tls"],
               "username": db_ctl.get("username") or hi.get("suggested_user"),
               "interval_seconds": int(data.get("interval_seconds") or 300)}
        try:
            if existing_db:
                changes = dict(cfg)
                if db_ctl.get("password"):
                    changes["password"] = db_ctl["password"]
                created_collectors.append(runner.update_collector(existing_db["id"], changes))
            else:
                created_collectors.append(runner.create(system["id"], {"type": "hana_sapcontrol", **cfg,
                                                                       "password": db_ctl.get("password")}))
        except CollectorError as exc:
            raise DiscoveryError(f"HANA instance collector: {exc}") from exc
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
    if os_method:
        persist_layers(systems.get_system(system["id"]), found)  # the inventory always records every layer found
        db_on_app_host = bool((found.get("database_layer") or {}).get("on_application_host"))
        # The application host serves the OS layer, plus the database process check when the database runs on it;
        # a separate database host serves both its OS metrics and that check.
        ssh_targets = [os_target if "os" in layers or ("database" in layers and db_on_app_host) else None,
                       db_target if layers & {"os", "database"} else None]
        for tgt in ssh_targets:
            if tgt is None:
                continue
            with get_db() as connection:
                existing_os = connection.execute(select(collectors).where(collectors.c.system_id == system["id"],
                                                                          collectors.c.type == "os_ssh",
                                                                          collectors.c.host == tgt.host)).mappings().first()
            cfg = {"host": tgt.host, "port": tgt.port, "use_tls": False, "verify_tls": False, "username": tgt.username,
                   "host_key": (found["host_key"] if tgt is os_target else found.get("db_host_key")),
                   "interval_seconds": max(int(data.get("interval_seconds") or 300), 900)}
            try:
                if existing_os:
                    created_collectors.append(runner.update_collector(existing_os["id"], {**cfg, "password": tgt.password, "private_key": tgt.private_key}))
                else:
                    created_collectors.append(runner.create(system["id"], {"type": "os_ssh", **cfg, "password": tgt.password,
                                                                            "private_key": tgt.private_key}))
            except CollectorError as exc:
                found["warnings"].append(f"OS inventory for {tgt.host} not scheduled: {exc}")
    covered = {layer for c in runner.list_for_system(system["id"]) for layer in c["layers"]}
    for layer in sorted(layers - covered, key=systems.LAYERS.index):
        found["warnings"].append(f"The {systems.LAYER_LABELS[layer]} layer is selected but has no collector yet; "
                                 "add one under the system's Setup tab.")
    first_runs = [runner.run(c["id"]) for c in created_collectors] if data.get("start_monitoring", True) else []
    return {"system": systems.get_system(system["id"]), "created": created, "instances": list_instances(system["id"]),
            "collectors": [runner.view(runner.get(c["id"])) for c in created_collectors],
            "first_runs": [{"collector_id": r["collector_id"], "status": r["status"], "error": r["error"]} for r in first_runs],
            "warnings": found["warnings"]}


# ----------------------------------------------------------------------------- OS-level discovery (SSH)
SIDADM_RE = re.compile(r"^([a-z][a-z0-9]{2})adm$")


def _sid_kind(instances: list[dict]) -> str:
    names = [i["instance_name"] or "" for i in instances]
    if names and all(n.startswith("SMDA") for n in names):
        return "agent"
    if names and all(n.startswith("HDB") for n in names):
        return "database"
    if names and all(re.match(r"^W\d\d$", n) for n in names):
        return "webdispatcher"
    return "application"


def _fallback_from_profiles(host: str, sid: str, instances: list[dict], profile: dict) -> dict:
    """When sapstartsrv cannot be reached, describe the system from the OS profiles alone."""
    rows = [{"hostname": (i["profile_host"] or host).lower(), "instance_nr": i["instance_nr"], "instance_name": i["instance_name"],
             "features": "", "http_port": None, "https_port": None, "start_priority": None, "status": "GRAY", "reachable": False}
            for i in instances if i["instance_nr"]]
    assign_roles(rows)
    dbms = DBMS_TYPES.get((profile.get("dbms/type") or "").lower())
    java = any(r["role"] in ("JAVA", "SCS") for r in rows) and not any(r["role"] in ("PAS", "AAS", "ASCS") for r in rows)
    return {"host": host, "sid": sid, "instances": rows, "stack": "JAVA" if java else "ABAP", "database": dbms,
            "db_host": profile.get("SAPDBHOST"), "product": "NW_JAVA" if java else "NW_ABAP", "product_version": None,
            "basis_release": None, "product_evidence": None, "components": [], "suggested_environment": suggest_environment(sid),
            "control_access": None, "collector": None, "hana_instance": None, "existing_systems": [], "warnings": [],
            "scanned_at": datetime.now(timezone.utc).isoformat()}


def _os_scan(data: dict):
    from .collectors import ssh

    host = (data.get("host") or "").strip().lower()
    if not host:
        raise DiscoveryError("Enter the hostname or IP address of an application server")
    port = int(data.get("ssh_port") or 22)
    os_user = (data.get("os_user") or "").strip()
    target = ssh.SshTarget(host, port, os_user, data.get("os_password") or None, data.get("os_private_key") or None,
                           data.get("expected_host_key") or None)
    try:
        inv = ssh.inventory(target)
    except CollectorError as exc:
        raise DiscoveryError(str(exc)) from exc
    warnings: list[str] = []
    grouped: dict[str, list[dict]] = {}
    for inst in inv["instances"]:
        grouped.setdefault(inst["sid"], []).append(inst)
    sids = [{"sid": sid, "kind": _sid_kind(insts), "instances": [i["instance_name"] for i in insts if i["instance_name"]]}
            for sid, insts in sorted(grouped.items())]
    apps = [s for s in sids if s["kind"] == "application"]
    if not apps:
        hana = [s["sid"] for s in sids if s["kind"] == "database"]
        if hana:
            raise DiscoveryError(f"{host} runs only the HANA database {', '.join(hana)}. Enter an application server "
                                 "(ASCS or PAS host); the database host is detected from the SAP profiles.")
        raise DiscoveryError(f"No SAP system found on {host}: /usr/sap/sapservices lists no instances.")
    sidadm = SIDADM_RE.match(os_user)
    requested = (data.get("sid") or "").upper()
    chosen = next((s for s in apps if s["sid"] == requested), None) \
        or next((s for s in apps if sidadm and s["sid"] == sidadm.group(1).upper()), None) or apps[0]
    sid = chosen["sid"]
    if requested and requested != sid:
        warnings.append(f"SID {requested} is not installed on {host}; using {sid}.")

    sap_user, sap_password = data.get("sap_user") or None, data.get("sap_password") or None
    if not sap_user and sidadm and sidadm.group(1).upper() == sid and data.get("os_password"):
        sap_user, sap_password = os_user, data["os_password"]  # <sid>adm works for SSH and sapstartsrv alike
    profile = inv["profiles"].get(sid, {})
    local_instances = grouped[sid]
    sap_result, last_error = None, None
    for inst in sorted(local_instances, key=lambda i: i["instance_nr"] or "99"):
        if not inst["instance_nr"]:
            continue
        try:
            sap_result = scan(host, sap_user, sap_password, bool(data.get("use_tls")), data.get("verify_tls", True), inst["instance_nr"])
            break
        except DiscoveryError as exc:
            last_error = exc
    if sap_result is None:
        sap_result = _fallback_from_profiles(host, sid, local_instances, profile)
        warnings.append(f"SAP Start Service not reachable ({last_error}); instances were read from the profiles and their state is unknown.")
    elif sap_result["sid"] != sid:
        warnings.append(f"sapstartsrv reports SID {sap_result['sid']} while the OS profiles list {sid}.")
    if not sap_user:
        warnings.append(f"Logged in as {os_user}: enter the {sid.lower()}adm password to enable protected monitoring and start/stop.")

    # Database layer: SAPDBHOST / dbms/type from DEFAULT.PFL, then the database host itself.
    os_info = inv["os"]
    db_type = DBMS_TYPES.get((profile.get("dbms/type") or "").lower()) or sap_result.get("database")
    db_host = (profile.get("SAPDBHOST") or profile.get("j2ee/dbhost") or sap_result.get("db_host") or "").strip().lower() or None
    local_names = {host, (os_info.get("hostname") or "").lower(), (os_info.get("fqdn") or "").lower(), *os_info.get("ip_addresses", [])}
    local_hana = [s for s in sids if s["kind"] == "database"]
    db_local = bool(db_host and (db_host in local_names or db_host.split(".")[0] in local_names)) or (db_type == "HANA" and bool(local_hana) and not db_host)
    db_inv, db_target = (inv if db_local else None), None
    if db_host and not db_local:
        db_user = (data.get("db_os_user") or "").strip() or os_user
        same = db_user == os_user and not (data.get("db_os_password") or data.get("db_os_private_key"))
        db_target = ssh.SshTarget(db_host, port, db_user, data.get("db_os_password") or (target.password if same else None),
                                  data.get("db_os_private_key") or (target.private_key if same else None),
                                  data.get("expected_db_host_key") or None)
        try:
            db_inv = ssh.inventory(db_target)
        except CollectorError as exc:
            db_target = None
            warnings.append(f"Database host {db_host} was not inventoried: {exc}. Enter its OS credentials to add the OS layer.")
    database = {"type": db_type, "host": db_host or host, "on_application_host": db_local,
                "tenant": profile.get("dbs/hdb/dbname"), "schema": profile.get("dbs/hdb/schema") or profile.get("dbs/ora/schema"),
                "db_sid": None, "instance_nr": None, "version": None, "status": None}
    if db_inv:
        hdb = [i for i in db_inv["instances"] if (i["instance_name"] or "").startswith("HDB")]
        if hdb and db_type in (None, "HANA"):
            database.update(type="HANA", db_sid=hdb[0]["sid"], instance_nr=hdb[0]["instance_nr"],
                            version=db_inv["hana_versions"].get(hdb[0]["sid"]))
        elif db_inv["other_databases"]:
            match = next((d for d in db_inv["other_databases"] if d["type"] == db_type), db_inv["other_databases"][0])
            database.update(db_sid=match["db_sid"], version=match["version"], type=db_type or match["type"])
        if db_inv["hostctrl_databases"]:
            hc = db_inv["hostctrl_databases"][0]
            database["status"] = hc.get("status")
            database["tenant"] = database["tenant"] or hc.get("name")
    if sap_result.get("database") is None and database["type"]:
        sap_result["database"] = database["type"]
    sap_warnings = [w for w in sap_result.get("warnings", []) if not w.startswith("No OS credentials given")]
    result = {**sap_result, "method": "os", "sid": sid, "sids_on_host": sids, "host_key": inv["host_key"],
              "os": os_info, "os_user": os_user, "database_layer": database,
              "db_os": db_inv["os"] if db_inv and not db_local else None,
              "db_host_key": db_inv["host_key"] if db_inv and not db_local else None,
              "sap_credentials": bool(sap_user), "warnings": sap_warnings + warnings}
    if not result.get("existing_systems"):
        result["existing_systems"] = _existing_systems(sid)
    return result, target, db_target, (sap_user, sap_password)


def os_scan(data: dict) -> dict:
    """Detect application, database and OS layers from one host login (root or <sid>adm)."""
    return _os_scan(data)[0]


def _upsert_asset(connection, system: dict, kind: str, name: str, hostname: str | None, attributes: dict) -> int:
    from .topology import _safe_attributes
    import json as _json

    attrs = _safe_attributes(attributes)
    encoded = _json.dumps(attrs, separators=(",", ":"), default=str)[:8000]
    cond = [topology_assets.c.system_id == system["id"], topology_assets.c.kind == kind, topology_assets.c.source == "discovery"]
    cond.append(topology_assets.c.hostname == hostname if hostname else topology_assets.c.name == name)
    row = connection.execute(select(topology_assets.c.id).where(*cond)).first()
    now = datetime.now(timezone.utc)
    if row:
        connection.execute(update(topology_assets).where(topology_assets.c.id == row[0]).values(
            name=name, attributes_json=encoded, updated_at=now, landscape=system["landscape"]))
        return row[0]
    return connection.execute(topology_assets.insert().values(
        landscape=system["landscape"], system_id=system["id"], kind=kind, name=name, hostname=hostname,
        attributes_json=encoded, source="discovery", updated_at=now).returning(topology_assets.c.id)).scalar_one()


def _link(connection, source: int, target: int, relationship: str) -> None:
    exists = connection.execute(select(topology_links.c.id).where(
        topology_links.c.source_asset_id == source, topology_links.c.target_asset_id == target,
        topology_links.c.relationship == relationship)).first()
    now = datetime.now(timezone.utc)
    if exists:
        connection.execute(update(topology_links).where(topology_links.c.id == exists[0]).values(verified_at=now))
    else:
        connection.execute(topology_links.insert().values(source_asset_id=source, target_asset_id=target, relationship=relationship,
                                                          source="discovery", verified_at=now,
                                                          notes="Read from the SAP profiles and the hosts during discovery"))


def _host_attributes(os_info: dict, host_key: str | None, role: str) -> dict:
    keep = ("fqdn", "ip_addresses", "os_name", "os_version", "os_pretty", "kernel", "arch", "cpu_count", "cpu_model",
            "memory_gb", "memory_used_pct", "swap_gb", "load1", "uptime_days", "virtualization", "timezone", "filesystems", "processes")
    attrs = {k: os_info.get(k) for k in keep}
    attrs.update(role=role, ssh_host_key=host_key, inventoried_at=datetime.now(timezone.utc).isoformat())
    return attrs


def persist_layers(system: dict, found: dict) -> None:
    """Record application, database and OS layers as discovery-sourced topology assets and links."""
    db = found.get("database_layer") or {}
    with get_db() as connection:
        app_host = _upsert_asset(connection, system, "host", found["os"].get("fqdn") or found["host"], found["host"],
                                 _host_attributes(found["os"], found.get("host_key"), "application" + (" + database" if db.get("on_application_host") else "")))
        application = _upsert_asset(connection, system, "application", f"{system['sid']} {system.get('product_label') or system['product']}", None, {
            "sid": system["sid"], "stack": found.get("stack"), "product": found.get("product"), "product_version": found.get("product_version"),
            "basis_release": found.get("basis_release"),
            "instances": [{"name": i.get("instance_name"), "nr": i["instance_nr"], "host": i["hostname"], "role": i["role"]} for i in found["instances"]][:40],
        })
        _link(connection, application, app_host, "runs_on")
        if db.get("type"):
            kind = "hana" if db["type"] == "HANA" else "database"
            database = _upsert_asset(connection, system, kind, f"{db.get('db_sid') or system['sid']} ({db['type']})", None, {
                "type": db["type"], "db_sid": db.get("db_sid"), "tenant": db.get("tenant"), "schema": db.get("schema"),
                "instance_nr": db.get("instance_nr"), "version": db.get("version"), "status": db.get("status"), "host": db.get("host"),
            })
            _link(connection, application, database, "uses_database")
            if db.get("on_application_host"):
                _link(connection, database, app_host, "runs_on")
            elif found.get("db_os"):
                db_host = _upsert_asset(connection, system, "host", found["db_os"].get("fqdn") or db["host"], db["host"],
                                        _host_attributes(found["db_os"], found.get("db_host_key"), "database"))
                _link(connection, database, db_host, "runs_on")


def update_host_asset(system: dict, hostname: str, os_info: dict, host_key: str | None, observed_role: str = "application") -> str:
    """Refresh a host's OS facts (called by the os_ssh collector) and return its role.

    The role recorded by discovery wins; a role observed on the host is added to it (never removed, so a database that
    stops does not turn its host into an application-only host and silence the process check)."""
    import json as _json

    with get_db() as connection:
        row = connection.execute(select(topology_assets).where(topology_assets.c.system_id == system["id"],
                                                               topology_assets.c.kind == "host",
                                                               topology_assets.c.hostname == hostname)).mappings().first()
        known = _json.loads(row["attributes_json"] or "{}").get("role") if row else None
        parts = {p.strip() for r in (known, observed_role) if r for p in r.split("+")}
        role = " + ".join(p for p in ("application", "database") if p in parts) or "application"
        _upsert_asset(connection, system, "host", os_info.get("fqdn") or hostname, hostname, _host_attributes(os_info, host_key, role))
    return role


def infrastructure(system_id: int) -> dict:
    """Application, database and OS layers of one system, as discovered."""
    import json as _json

    system = systems.get_system(system_id)
    with get_db() as connection:
        assets = [dict(r) for r in connection.execute(select(topology_assets).where(topology_assets.c.system_id == system_id)
                                                      .order_by(topology_assets.c.kind, topology_assets.c.id)).mappings().all()]
        ids = [a["id"] for a in assets]
        links = [dict(r) for r in connection.execute(select(topology_links).where(topology_links.c.source_asset_id.in_(ids)))
                 .mappings().all()] if ids else []
        os_collectors = [runner.view(r) for r in connection.execute(select(collectors).where(
            collectors.c.system_id == system_id, collectors.c.type == "os_ssh")).mappings().all()]
    for a in assets:
        a["attributes"] = _json.loads(a.pop("attributes_json") or "{}")
        for k in ("created_at", "updated_at"):
            a[k] = a[k].isoformat() if hasattr(a.get(k), "isoformat") else a.get(k)
    for link in links:
        for k in ("created_at", "verified_at"):
            link[k] = link[k].isoformat() if hasattr(link.get(k), "isoformat") else link.get(k)
    return {"system": {"id": system["id"], "sid": system["sid"], "product_label": system.get("product_label"),
                       "stack": system["stack"], "database": system["database"], "host": system.get("host")},
            "application": next((a for a in assets if a["kind"] == "application"), None),
            "database": next((a for a in assets if a["kind"] in ("hana", "database")), None),
            "hosts": [a for a in assets if a["kind"] == "host"],
            "instances": list_instances(system_id), "links": links, "collectors": os_collectors}


def refresh_infrastructure(system_id: int) -> dict:
    with get_db() as connection:
        ids = [r[0] for r in connection.execute(select(collectors.c.id).where(collectors.c.system_id == system_id,
                                                                              collectors.c.type == "os_ssh")).all()]
    if not ids:
        raise DiscoveryError("No OS inventory is configured. Run discovery with an OS login (root or <sid>adm) first.")
    results = [runner.run(i) for i in ids]
    out = infrastructure(system_id)
    out["refresh"] = [{"collector_id": r["collector_id"], "status": r["status"], "error": r["error"]} for r in results]
    return out
