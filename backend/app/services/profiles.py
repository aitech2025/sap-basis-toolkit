"""Product profiles: what each SAP product/stack/database supports and how to export data from it.

The toolkit is product-agnostic; everything product-specific lives here as data.
Operators can add products, housekeeping entries or export notes without code
changes through a JSON file referenced by PROFILE_OVERRIDES_FILE, e.g.

    {"products": {"MDG": {"label": "SAP MDG", "stacks": ["ABAP"], "license_model": "NAMED_USER"}},
     "housekeeping": {"ZLOG_TABLE": "Delete with report ZLOG_CLEANUP"}}
"""
import json
import logging
from copy import deepcopy
from functools import lru_cache

from .. import config

logger = logging.getLogger(__name__)

STACKS = {"ABAP": "AS ABAP", "JAVA": "AS Java", "DUAL": "Dual stack (ABAP + Java)"}
DATABASES = {
    "HANA": "SAP HANA", "ORACLE": "Oracle", "DB2": "IBM Db2 (LUW)", "DB4": "IBM Db2 for i",
    "DB6Z": "IBM Db2 for z/OS", "MSSQL": "Microsoft SQL Server", "MAXDB": "SAP MaxDB", "ASE": "SAP ASE (Sybase)",
}
ENVIRONMENTS = {"DEV": "Development", "QAS": "Quality / Test", "PRE": "Pre-production",
                "PRD": "Production", "SBX": "Sandbox", "TRN": "Training", "OTHER": "Other"}

LICENSE_MODELS = {
    "NAMED_USER": {
        "label": "Classic named-user licensing",
        "tiers": {"full": "Professional", "limited": "Limited Professional", "self_service": "Employee"},
        "weights": {"full": 1.0, "limited": 0.5, "self_service": 0.1},
        "unit": "relative licence cost",
        "note": "Relative weights are indicative only; use your price list for the real saving.",
    },
    "FUE": {
        "label": "Full Use Equivalent (FUE)",
        "tiers": {"full": "Advanced", "limited": "Core", "self_service": "Self-Service"},
        "weights": {"full": 1.0, "limited": 1 / 5, "self_service": 1 / 30},
        "unit": "FUE",
        "note": "Typical FUE ratios 1 / 0.2 / 0.033; verify against your SAP contract.",
    },
}

ABAP_STACKS = ("ABAP", "DUAL")

PRODUCTS = {
    "S4HANA": {"label": "SAP S/4HANA", "stacks": ["ABAP"], "databases": ["HANA"], "license_model": "FUE"},
    "ECC": {"label": "SAP ERP 6.0 (ECC)", "stacks": ["ABAP", "DUAL"], "license_model": "NAMED_USER"},
    "BW4HANA": {"label": "SAP BW/4HANA", "stacks": ["ABAP"], "databases": ["HANA"], "license_model": "NAMED_USER",
                "housekeeping": ["BW"]},
    "BW": {"label": "SAP BW 7.x", "stacks": ["ABAP"], "license_model": "NAMED_USER", "housekeeping": ["BW"]},
    "CRM": {"label": "SAP CRM", "stacks": ["ABAP"], "license_model": "NAMED_USER", "housekeeping": ["CRM"]},
    "SRM": {"label": "SAP SRM", "stacks": ["ABAP"], "license_model": "NAMED_USER"},
    "SCM": {"label": "SAP SCM / APO", "stacks": ["ABAP"], "license_model": "NAMED_USER"},
    "SOLMAN": {"label": "SAP Solution Manager 7.2", "stacks": ["ABAP", "JAVA", "DUAL"], "license_model": "NAMED_USER"},
    "GRC": {"label": "SAP GRC Access Control", "stacks": ["ABAP"], "license_model": "NAMED_USER"},
    "GATEWAY": {"label": "SAP Gateway / Fiori front-end", "stacks": ["ABAP"], "license_model": "NAMED_USER"},
    "PI_PO": {"label": "SAP PI / PO", "stacks": ["JAVA", "DUAL"], "license_model": "NAMED_USER", "housekeeping": ["PI"]},
    "PORTAL": {"label": "SAP Enterprise Portal", "stacks": ["JAVA"], "license_model": "NAMED_USER"},
    "NW_ABAP": {"label": "SAP NetWeaver AS ABAP (other)", "stacks": ["ABAP"], "license_model": "NAMED_USER"},
    "NW_JAVA": {"label": "SAP NetWeaver AS Java (other)", "stacks": ["JAVA"], "license_model": "NAMED_USER"},
}

# Typical housekeeping. Always validate against your release and retention policy.
HOUSEKEEPING_BASE = {
    "BALHDR": "Application log: delete expired logs with SLG2 / report SBAL_DELETE (schedule daily).",
    "BALDAT": "Application log data: delete with SLG2 / SBAL_DELETE; check log expiry dates set by applications.",
    "BALC": "Application log context: cleaned together with BALHDR/BALDAT via SLG2.",
    "SOFFCONT1": "SAPoffice/BCS document content: reorganise with RSBCS_REORG and move attachments to a content server.",
    "SOC3": "SAPoffice content: reorganise with RSBCS_REORG / SAPoffice reorg reports.",
    "EDIDC": "IDoc control records: archive with archiving object IDOC (SARA); define retention per message type.",
    "EDIDS": "IDoc status records: archive with archiving object IDOC (SARA).",
    "EDI40": "IDoc data records: archive with archiving object IDOC (SARA).",
    "DBTABLOG": "Table change logging: delete old logs with RSTBPDEL (SCU3); review rec/client logging scope.",
    "CDHDR": "Change documents: archive with archiving object CHANGEDOCU.",
    "CDPOS": "Change document items: archive with archiving object CHANGEDOCU.",
    "TST03": "Spool data: schedule RSPO1041 (or RSPO0041) spool reorganisation.",
    "TSP01": "Spool requests: schedule RSPO1041 spool reorganisation.",
    "APQD": "Batch input data: delete processed sessions with RSBDCREO.",
    "APQI": "Batch input queue: delete processed sessions with RSBDCREO.",
    "TBTCO": "Background job headers: schedule RSBTCDEL2 to delete old job logs.",
    "TBTCP": "Background job steps: schedule RSBTCDEL2.",
    "BDCP2": "Change pointers: delete processed pointers with RBDCPCLR2.",
    "ARFCSDATA": "tRFC payload: investigate stuck LUWs in SM58 before cleanup; do not delete blindly.",
    "SWWLOGHIST": "Workflow logs: archive with archiving object WORKITEM.",
    "SWWWIHEAD": "Workflow work items: archive with archiving object WORKITEM.",
    "VBDATA": "Update records: investigate failed updates in SM13; never delete without functional sign-off.",
}
HOUSEKEEPING_EXTRA = {
    "BW": {
        "RSDDSTATLOGGING": "BW statistics: delete old statistics data (RSDDSTAT_DATA_DELETE).",
        "RSBERRORLOG": "DTP error log: delete old entries with RSBM_ERRORLOG_DELETE.",
    },
    "CRM": {"SMW3_BDOC": "BDoc messages: reorganise with SMO6_REORG."},
    "PI": {
        "SXMSCLUP": "PI ABAP message payloads: verify archiving/deletion jobs and retention in SXMB_ADM.",
        "SXMSCLUR": "PI ABAP message payloads: verify archiving/deletion jobs and retention in SXMB_ADM.",
        "BC_MSG": "Adapter Engine messages: check archiving/deletion jobs in NWA (Java Scheduler) and retention.",
        "BC_MSG_AUDIT": "Adapter Engine audit log: check deletion job and audit log retention in NWA.",
    },
}

GROWTH_EXPORTS = {
    "HANA": ("Run in HANA Studio / DBACOCKPIT SQL console and export to CSV:\n"
             "SELECT TABLE_NAME, SUM(MEMORY_SIZE_IN_TOTAL) AS MEMORY_SIZE_IN_TOTAL, SUM(RECORD_COUNT) AS RECORD_COUNT\n"
             "FROM M_CS_TABLES WHERE SCHEMA_NAME = '<SAP schema>' GROUP BY TABLE_NAME;\n"
             "Capacity: data volume size/used from M_VOLUME_FILES or DB02 → Overview; memory from M_HOST_RESOURCE_UTILIZATION."),
    "ORACLE": ("Run as a DBA user (or DB02 → Segments → export):\n"
               "SELECT SEGMENT_NAME AS TABLE_NAME, SUM(BYTES) AS SIZE_BYTES FROM DBA_SEGMENTS\n"
               "WHERE OWNER = '<SAP schema>' AND SEGMENT_TYPE LIKE 'TABLE%' GROUP BY SEGMENT_NAME;\n"
               "Capacity: tablespace totals from DB02 → Space → Tablespaces."),
    "MSSQL": ("Run in SQL Server Management Studio against the SAP database:\n"
              "SELECT t.name AS TABLE_NAME, SUM(ps.reserved_page_count) * 8 AS SIZE_KB,\n"
              "SUM(CASE WHEN ps.index_id < 2 THEN ps.row_count END) AS ROW_COUNT\n"
              "FROM sys.dm_db_partition_stats ps JOIN sys.tables t ON t.object_id = ps.object_id GROUP BY t.name;"),
    "DB2": ("Run with db2 CLI and export to CSV:\n"
            "SELECT TABNAME AS TABLE_NAME, DATA_OBJECT_P_SIZE + INDEX_OBJECT_P_SIZE + LONG_OBJECT_P_SIZE + LOB_OBJECT_P_SIZE AS SIZE_KB\n"
            "FROM SYSIBMADM.ADMINTABINFO WHERE TABSCHEMA = '<SAP schema>';"),
}
GROWTH_EXPORT_DEFAULT = ("DB02 (or DBACOCKPIT) → Space → Tables/Segments → export to spreadsheet with the table "
                         "name and size column (MB, KB or bytes). Record capacity totals from the same transaction.")

USERS_EXPORTS = {
    "ABAP": ("SE16N on USR02 with fields BNAME, USTYP, UFLAG, TRDAT, ERDAT, GLTGB, CLASS → export. "
             "Add a license column (e.g. USR06-LIC_TYPE or your USMM/SLIM classification) as LIC_TYPE. "
             "Optional usage: ST03N → Expert mode → User and Billing Statistics → User Profile / Transaction Profile, "
             "export user, transaction and dialog steps for the last 3 months."),
    "JAVA": ("NetWeaver Administrator → Identity Management: export users (or UME batch export) to CSV with "
             "Logon ID, Last Successful Logon and Locked columns. Java has no ST03N; license recommendations "
             "need a usage export from your access logs."),
}
TRANSPORT_EXPORTS = {
    "ABAP": ("SE16N on E071 (TRKORR, PGMID, OBJECT, OBJ_NAME, OBJFUNC) and E070 (TRKORR, TRSTATUS, AS4DATE, "
             "AS4TIME, AS4USER, STRKORR) in the source system, filtered to the requests you plan to import. "
             "Target history: STMS → Import History export or table TPALOG. Post-import: STMS import history "
             "export (TRKORR, RC) or the tp/ALOG text."),
    "JAVA": "Java transports run through CTS+ / NWDI; the E070/E071 checker does not apply to pure Java systems.",
}
CERT_EXPORTS = {
    "ABAP": ("STRUST → open each PSE (SSL server, SSL client, SAML, WS-Security, System PSE) → double-click the "
             "certificate → Export (Base64). Or `sapgenpse maintain_pk -l -p <PSE>` for trusted CAs. "
             "Register HTTPS endpoints (ICM ports, Web Dispatcher) for live monitoring."),
    "JAVA": ("NetWeaver Administrator → Certificates and Keys → Key Storage: export certificates of each view "
             "(ICM_SSL_*, TrustedCAs, ...). Register HTTPS endpoints for live monitoring."),
}


@lru_cache(maxsize=1)
def _overrides() -> dict:
    path = config.PROFILE_OVERRIDES_FILE
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        logger.exception("Could not load PROFILE_OVERRIDES_FILE %s", path)
        return {}


def products() -> dict:
    merged = deepcopy(PRODUCTS)
    for key, value in (_overrides().get("products") or {}).items():
        merged.setdefault(key.upper(), {}).update(value)
    return merged


def product(key: str) -> dict | None:
    return products().get((key or "").upper())


def validate_system(product_key: str, stack: str, database: str, license_model: str | None) -> list[str]:
    errors = []
    prod = product(product_key)
    if prod is None:
        errors.append(f"Unknown product '{product_key}'. Known: {', '.join(products())}")
        return errors
    if stack not in STACKS:
        errors.append(f"Unknown stack '{stack}'")
    elif stack not in prod.get("stacks", list(STACKS)):
        errors.append(f"{prod['label']} does not run on stack {stack} (allowed: {', '.join(prod['stacks'])})")
    if database not in DATABASES:
        errors.append(f"Unknown database '{database}'")
    elif prod.get("databases") and database not in prod["databases"]:
        errors.append(f"{prod['label']} requires database {', '.join(prod['databases'])}")
    if license_model and license_model not in LICENSE_MODELS:
        errors.append(f"Unknown license model '{license_model}'")
    return errors


def effective_license_model(system: dict) -> str:
    return system.get("license_model") or (product(system["product"]) or {}).get("license_model", "NAMED_USER")


def housekeeping_catalogue(product_key: str) -> dict[str, str]:
    catalogue = dict(HOUSEKEEPING_BASE)
    for group in (product(product_key) or {}).get("housekeeping", []):
        catalogue.update(HOUSEKEEPING_EXTRA.get(group, {}))
    catalogue.update({k.upper(): v for k, v in (_overrides().get("housekeeping") or {}).items()})
    return catalogue


def capabilities(system: dict) -> dict:
    stack = system["stack"]
    abap = stack in ABAP_STACKS
    model = effective_license_model(system)
    return {
        "certificates": {"supported": True, "how_to_export": CERT_EXPORTS["ABAP" if abap else "JAVA"]},
        "growth": {"supported": True,
                   "how_to_export": GROWTH_EXPORTS.get(system["database"], GROWTH_EXPORT_DEFAULT)},
        "users": {"supported": True, "source": "USR02" if abap else "UME",
                  "license_recommendations": abap,
                  "how_to_export": USERS_EXPORTS["ABAP" if abap else "JAVA"]},
        "transports": {"supported": abap, "how_to_export": TRANSPORT_EXPORTS["ABAP" if abap else "JAVA"]},
        "license_model": {"key": model, **LICENSE_MODELS[model]},
    }


def catalogue() -> dict:
    return {
        "products": [{"key": k, **v} for k, v in products().items()],
        "stacks": [{"key": k, "label": v} for k, v in STACKS.items()],
        "databases": [{"key": k, "label": v} for k, v in DATABASES.items()],
        "environments": [{"key": k, "label": v} for k, v in ENVIRONMENTS.items()],
        "license_models": [{"key": k, "label": v["label"], "tiers": v["tiers"]} for k, v in LICENSE_MODELS.items()],
    }
