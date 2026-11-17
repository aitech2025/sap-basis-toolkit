"""Inactive user & license optimizer (stateless: uploads are analysed, never stored).

Inputs
------
* USR02 export (SE16/SE16N or SUIM): BNAME, TRDAT (last logon), UFLAG (lock),
  USTYP (A dialog, B system, C communication, L reference, S service),
  optional ERDAT (created), GLTGB (valid to), CLASS (user group) and a current
  license type column (USR06-LIC_TYPE or your own classification).
* Optional ST03N user-profile/transaction export: user, transaction/report,
  optional dialog-step count. Used to recommend a license downgrade.

The tool recommends; locking is executed by an administrator (SU10 mass lock or
the generated BAPI_USER_LOCK list), keeping a human in the loop for compliance.
"""
from collections import defaultdict
from datetime import date

from .. import config
from .profiles import LICENSE_MODELS
from .tabular import parse_number, parse_sap_date, pick, read_rows, require_columns

USER_ALIASES = ("BNAME", "User", "User Name", "USERNAME", "Account", "ACCOUNT", "Logon ID", "LOGONID", "uniquename")
LAST_LOGON_ALIASES = ("TRDAT", "Last Logon", "Last Logon Date", "LAST_LOGON", "LastLogonDate",
                      "Last Successful Logon", "lastsuccessfullogon")
LOCK_ALIASES = ("UFLAG", "Lock", "Lock Status", "LOCKED", "islocked", "Account Locked")
TYPE_ALIASES = ("USTYP", "User Type", "UTYPE")
CREATED_ALIASES = ("ERDAT", "Created On", "Created")
VALID_TO_ALIASES = ("GLTGB", "Valid To", "Valid Through")
GROUP_ALIASES = ("CLASS", "User Group")
LICENSE_ALIASES = ("LIC_TYPE", "License Type", "LICENSE", "Contractual User Type", "Current License")
TCODE_ALIASES = ("TCODE", "Transaction", "Transaction Code", "Report", "ENTRY_ID", "Program")
STEPS_ALIASES = ("Dialog Steps", "DIALOG_STEPS", "STEPS", "Count", "Executions")

USER_TYPE_NAMES = {"A": "Dialog", "B": "System", "C": "Communication", "L": "Reference", "S": "Service"}

# Self-service transactions (ESS-style). Extend to match your landscape.
SELF_SERVICE_TCODES = {
    "SU3", "SU53", "SU56", "SBWP", "SO01", "SO02", "SMEN", "S000", "SESSION_MANAGER", "CAT2", "CATS_DA",
    "PR05", "PTARQ", "PZ01", "PZ04", "PA20", "HRESSIN_F16", "ME51N", "ME53N", "SPRO_ESS", "NWBC", "/UI2/FLP",
    "BBPSC01", "SPAD_USER", "SP01", "SP02", "SM04_SELF",
}
# Display-only patterns: SAP display transactions conventionally end in 03 / 3N, or start with S_ALR/ reports.
DISPLAY_SUFFIXES = ("03", "03N", "3N", "23N", "33", "43")
DISPLAY_PREFIXES = ("S_ALR_", "S_P99_", "S_PL0_", "MB5", "MB51", "MB52", "ME2", "FBL", "KSB", "MMBE", "VA05", "VF05", "MD04", "CO03", "CJI3")
DISPLAY_TCODES = {"SE16N_DISPLAY", "SQVI", "SUIM", "ST22", "SM37", "SM21", "AL11"}



def _locked(flag: str) -> bool:
    value = (flag or "0").strip().upper()
    return value not in ("", "0", "N", "NO", "FALSE", "NOT LOCKED", "UNLOCKED")


def _protected(user: str) -> bool:
    for pattern in config.PROTECTED_USERS:
        if pattern.endswith("*") and user.startswith(pattern[:-1]):
            return True
        if user == pattern:
            return True
    return False


def _is_display(tcode: str) -> bool:
    return (
        tcode in DISPLAY_TCODES
        or tcode.endswith(DISPLAY_SUFFIXES)
        or tcode.startswith(DISPLAY_PREFIXES)
    )


def classify_usage(tcodes: set[str]) -> str:
    """Return the license tier key (full / limited / self_service) implied by observed usage."""
    if not tcodes or tcodes <= SELF_SERVICE_TCODES:
        return "self_service"
    if all(t in SELF_SERVICE_TCODES or _is_display(t) for t in tcodes):
        return "limited"
    return "full"


def normalise_license(value: str) -> str | None:
    """Map classic (Professional/Limited Professional/Employee) or FUE (Advanced/Core/Self-Service) wording."""
    v = (value or "").strip().upper()
    if not v:
        return None
    if any(k in v for k in ("LIMITED", "CORE", "FUNCTIONAL")):
        return "limited"
    if any(k in v for k in ("PROF", "ADVANCED", "DEVELOPER")):  # before ESS: "PROFESSIONAL" contains "ESS"
        return "full"
    if any(k in v for k in ("EMPLOYEE", "ESS", "SELF")):
        return "self_service"
    return None


def parse_usage(raw: bytes | None) -> dict[str, set[str]]:
    usage: dict[str, set[str]] = defaultdict(set)
    if not raw:
        return usage
    rows = read_rows(raw)
    require_columns(rows, {"user": USER_ALIASES, "transaction": TCODE_ALIASES})
    for row in rows:
        user = pick(row, *USER_ALIASES).upper()
        tcode = pick(row, *TCODE_ALIASES).upper()
        steps = parse_number(pick(row, *STEPS_ALIASES))
        if user and tcode and (steps is None or steps > 0):
            usage[user].add(tcode)
    return usage


def analyse(usr02_raw: bytes, usage_raw: bytes | None = None, today: date | None = None,
            dormant_days: int | None = None, license_model: str = "NAMED_USER",
            license_recommendations: bool = True) -> dict:
    model = LICENSE_MODELS[license_model]
    tiers, weights = model["tiers"], model["weights"]
    today = today or date.today()
    dormant_days = dormant_days or config.DORMANT_USER_DAYS
    rows = read_rows(usr02_raw)
    require_columns(rows, {"user": USER_ALIASES, "last logon": LAST_LOGON_ALIASES})
    usage = parse_usage(usage_raw)

    users, lock_candidates, never_logged_on, license_changes = [], [], [], []
    excluded = defaultdict(int)
    for row in rows:
        user = pick(row, *USER_ALIASES).upper()
        if not user:
            continue
        user_type = pick(row, *TYPE_ALIASES, default="A").upper()[:1] or "A"
        locked = _locked(pick(row, *LOCK_ALIASES))
        last_logon = parse_sap_date(pick(row, *LAST_LOGON_ALIASES))
        created = parse_sap_date(pick(row, *CREATED_ALIASES))
        valid_to = parse_sap_date(pick(row, *VALID_TO_ALIASES))
        current_tier = normalise_license(pick(row, *LICENSE_ALIASES))
        days_inactive = (today - last_logon).days if last_logon else None
        expired = bool(valid_to and valid_to < today)

        record = {
            "user": user,
            "user_type": USER_TYPE_NAMES.get(user_type, user_type),
            "user_group": pick(row, *GROUP_ALIASES),
            "locked": locked,
            "last_logon": last_logon.isoformat() if last_logon else None,
            "days_inactive": days_inactive,
            "valid_to": valid_to.isoformat() if valid_to else None,
            "current_license": tiers[current_tier] if current_tier else None,
            "action": "none",
            "reason": "",
        }

        if _protected(user):
            excluded["protected"] += 1
            record["reason"] = "protected technical user"
        elif user_type != "A":
            excluded["non_dialog"] += 1
            record["reason"] = "non-dialog user (not license-relevant for dormancy locking)"
        elif locked:
            excluded["already_locked"] += 1
            record["reason"] = "already locked"
        elif last_logon is None:
            age = (today - created).days if created else None
            if age is None or age > dormant_days:
                record.update(action="lock", reason=f"never logged on (created {age} days ago)" if age is not None else "never logged on")
                never_logged_on.append(record)
                lock_candidates.append(record)
            else:
                record["reason"] = f"new user, created {age} days ago"
        elif days_inactive >= dormant_days or expired:
            record.update(action="lock", reason="validity expired" if expired and days_inactive < dormant_days
                          else f"inactive for {days_inactive} days")
            lock_candidates.append(record)

        # Recommend only from observed usage: no ST03N rows means "unknown", not "self-service".
        if (license_recommendations and user_type == "A" and record["action"] != "lock" and not locked
                and user in usage and not _protected(user)):
            recommended = classify_usage(usage[user])
            record["recommended_license"] = tiers[recommended]
            if current_tier and weights[recommended] < weights[current_tier]:
                record["license_action"] = "downgrade"
                license_changes.append({
                    "user": user, "current_license": tiers[current_tier], "recommended_license": tiers[recommended],
                    "transactions_used": sorted(usage.get(user, set()))[:25],
                    "saving": round(weights[current_tier] - weights[recommended], 3),
                })
        record["_tier"] = current_tier
        users.append(record)

    dialog_active = [u for u in users if u["user_type"] == "Dialog" and not u["locked"]]
    lock_saving = sum(weights[u["_tier"] or "full"] for u in lock_candidates)
    downgrade_saving = sum(c["saving"] for c in license_changes)
    for u in users:
        u.pop("_tier", None)
    return {
        "as_of": today.isoformat(),
        "dormant_days": dormant_days,
        "summary": {
            "users_analysed": len(users),
            "active_dialog_users": len(dialog_active),
            "lock_candidates": len(lock_candidates),
            "never_logged_on": len(never_logged_on),
            "license_downgrades": len(license_changes),
            "excluded": dict(excluded),
            "license_model": license_model,
            "estimated_saving": round(lock_saving + downgrade_saving, 2),
            "saving_unit": model["unit"],
            "saving_note": model["note"],
        },
        "lock_candidates": lock_candidates,
        "license_changes": sorted(license_changes, key=lambda c: -c["saving"]),
        "su10_user_list": "\n".join(u["user"] for u in lock_candidates),
    }
