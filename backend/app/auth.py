"""Users, roles, sessions and audit trail.

Roles (each includes the previous):
  viewer   – read everything
  operator – run checks/uploads, acknowledge/resolve incidents, maintenance windows
  admin    – manage systems' credentials/collectors, alert rules, users; read the audit log

Sessions are stateless HMAC-signed tokens. Each user has a token_version: changing a password,
the role or disabling the user bumps it and invalidates existing sessions. The API key
(API_AUTH_TOKEN) remains valid as an admin service account for automation and scripts.
"""
import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import delete, func, select, update

from . import config
from .db import audit_log, get_db, users

ROLES = ("viewer", "operator", "admin")
RANK = {role: i for i, role in enumerate(ROLES)}
MIN_PASSWORD_LENGTH = 12

_api_key = APIKeyHeader(name="X-API-Key", auto_error=False)
_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    username: str
    role: str
    kind: str  # user | apikey

    def can(self, role: str) -> bool:
        return RANK[self.role] >= RANK[role]


# ------------------------------------------------------------------------------- secrets
def secret_key() -> bytes:
    if config.SECRET_KEY:
        return hashlib.sha256(config.SECRET_KEY.encode()).digest()
    if config.API_AUTH_TOKEN:
        return hashlib.sha256(b"sap-basis-toolkit:" + config.API_AUTH_TOKEN.encode()).digest()
    raise HTTPException(503, "Server is locked: configure SECRET_KEY (or API_AUTH_TOKEN)")


# ------------------------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                                dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def validate_password(password: str, username: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(422, f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if username.lower() in password.lower():
        raise HTTPException(422, "Password must not contain the username")


# ------------------------------------------------------------------------------- tokens
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def issue_token(user: dict) -> tuple[str, datetime]:
    expires = datetime.now(timezone.utc) + timedelta(hours=config.SESSION_HOURS)
    payload = _b64(json.dumps({"u": user["username"], "v": user["token_version"], "exp": int(expires.timestamp())},
                              separators=(",", ":")).encode())
    signature = _b64(hmac.new(secret_key(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}", expires


def read_token(token: str) -> dict | None:
    try:
        payload, signature = token.split(".", 1)
        expected = _b64(hmac.new(secret_key(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(_unb64(payload))
        if data["exp"] < time.time():
            return None
        return data
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


# ------------------------------------------------------------------------------- users
def _public(row) -> dict:
    item = {k: row[k] for k in ("id", "username", "display_name", "email", "role", "active", "created_at", "last_login_at")}
    item["active"] = bool(item["active"])
    for k in ("created_at", "last_login_at"):
        if hasattr(item[k], "isoformat"):
            item[k] = item[k].isoformat()
    return item


def user_count() -> int:
    with get_db() as connection:
        return connection.execute(select(func.count()).select_from(users)).scalar_one()


def get_user(username: str) -> dict | None:
    with get_db() as connection:
        row = connection.execute(select(users).where(users.c.username == username)).mappings().first()
    return dict(row) if row else None


def list_users() -> list[dict]:
    with get_db() as connection:
        rows = connection.execute(select(users).order_by(users.c.username)).mappings().all()
    return [_public(r) for r in rows]


def create_user(username: str, password: str, role: str, display_name: str | None = None,
                email: str | None = None) -> dict:
    username = username.strip().lower()
    if not username or not all(ch.isalnum() or ch in "._-@" for ch in username) or len(username) > 64:
        raise HTTPException(422, "Username may contain letters, digits and . _ - @ (max 64)")
    if role not in ROLES:
        raise HTTPException(422, f"Role must be one of {', '.join(ROLES)}")
    validate_password(password, username)
    if get_user(username):
        raise HTTPException(409, f"User {username} already exists")
    with get_db() as connection:
        row = connection.execute(users.insert().values(
            username=username, password_hash=hash_password(password), role=role,
            display_name=(display_name or "").strip() or None, email=(email or "").strip() or None,
        ).returning(users)).mappings().one()
    return _public(row)


def update_user(user_id: int, changes: dict, actor: Principal) -> dict:
    with get_db() as connection:
        row = connection.execute(select(users).where(users.c.id == user_id)).mappings().first()
        if row is None:
            raise HTTPException(404, "User not found")
        values: dict = {}
        if "role" in changes and changes["role"] is not None:
            if changes["role"] not in ROLES:
                raise HTTPException(422, f"Role must be one of {', '.join(ROLES)}")
            values["role"] = changes["role"]
        if "active" in changes and changes["active"] is not None:
            values["active"] = changes["active"]
        for k in ("display_name", "email"):
            if k in changes:
                values[k] = (changes[k] or "").strip() or None
        if changes.get("password"):
            validate_password(changes["password"], row["username"])
            values["password_hash"] = hash_password(changes["password"])
        demoting_self = row["username"] == actor.username and (
            values.get("role", row["role"]) != "admin" or values.get("active", True) is False)
        if demoting_self:
            raise HTTPException(422, "You cannot remove your own admin role or disable yourself")
        if row["role"] == "admin" and (values.get("role", "admin") != "admin" or values.get("active") is False):
            admins = connection.execute(select(func.count()).select_from(users).where(
                users.c.role == "admin", users.c.active.is_(True))).scalar_one()
            if admins <= 1:
                raise HTTPException(422, "At least one active admin is required")
        if {"role", "active", "password_hash"} & values.keys():
            values["token_version"] = row["token_version"] + 1  # end existing sessions
        if values:
            connection.execute(update(users).where(users.c.id == user_id).values(**values))
        row = connection.execute(select(users).where(users.c.id == user_id)).mappings().one()
    return _public(row)


def bootstrap_admin() -> None:
    """Create the first admin from ADMIN_PASSWORD when the user table is empty."""
    if config.ADMIN_PASSWORD and user_count() == 0:
        create_user(config.ADMIN_USERNAME or "admin", config.ADMIN_PASSWORD, "admin", "Administrator")


# ------------------------------------------------------------------------------- login throttling
_failures: dict[str, list[float]] = {}
_failures_lock = threading.Lock()
WINDOW_SECONDS = 15 * 60


def _throttle_key(username: str, ip: str) -> str:
    return f"{username.lower()}|{ip}"


def check_throttle(username: str, ip: str) -> None:
    with _failures_lock:
        recent = [t for t in _failures.get(_throttle_key(username, ip), []) if t > time.time() - WINDOW_SECONDS]
        _failures[_throttle_key(username, ip)] = recent
        if len(recent) >= config.LOGIN_MAX_FAILURES:
            raise HTTPException(429, "Too many failed sign-in attempts; try again in 15 minutes")


def record_failure(username: str, ip: str) -> None:
    with _failures_lock:
        _failures.setdefault(_throttle_key(username, ip), []).append(time.time())


def clear_failures(username: str, ip: str) -> None:
    with _failures_lock:
        _failures.pop(_throttle_key(username, ip), None)


def authenticate_password(username: str, password: str, ip: str) -> dict:
    username = username.strip().lower()
    check_throttle(username, ip)
    user = get_user(username)
    # Always run a hash comparison so response time does not reveal whether the user exists.
    ok = verify_password(password, user["password_hash"] if user else hash_password(secrets.token_hex(8)))
    if not user or not ok or not user["active"]:
        record_failure(username, ip)
        audit(username or "?", "login.failed", None, 401, None, ip)
        raise HTTPException(401, "Invalid username or password")
    clear_failures(username, ip)
    with get_db() as connection:
        connection.execute(update(users).where(users.c.id == user["id"]).values(last_login_at=datetime.now(timezone.utc)))
    audit(username, "login", None, 200, None, ip)
    return user


# ------------------------------------------------------------------------------- request auth
def current_principal(
    request: Request,
    api_key: str | None = Security(_api_key),
    bearer: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> Principal:
    if api_key is not None:
        if not config.API_AUTH_TOKEN:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                "API is locked: configure API_AUTH_TOKEN before using protected endpoints")
        if not secrets.compare_digest(api_key, config.API_AUTH_TOKEN):
            raise HTTPException(401, "Invalid or missing API key", headers={"WWW-Authenticate": "ApiKey"})
        principal = Principal("api-key", "admin", "apikey")
    elif bearer is not None:
        data = read_token(bearer.credentials)
        user = get_user(data["u"]) if data else None
        if not user or not user["active"] or user["token_version"] != data["v"]:
            raise HTTPException(401, "Session expired or invalid; sign in again", headers={"WWW-Authenticate": "Bearer"})
        principal = Principal(user["username"], user["role"], "user")
    else:
        raise HTTPException(401, "Sign in or provide an API key", headers={"WWW-Authenticate": "Bearer"})
    request.state.principal = principal
    return principal


# Paths whose mutations need admin (credentials, rules, users, discovery, start/stop); everything else needs operator.
ADMIN_WRITE_PREFIXES = ("/api/v1/users", "/api/v1/collectors", "/api/v1/rules", "/api/v1/discovery")
ADMIN_READ_PREFIXES = ("/api/v1/users", "/api/v1/audit")


def authorize(request: Request, principal: Principal = Depends(current_principal)) -> Principal:
    path = request.url.path
    if request.method in ("GET", "HEAD", "OPTIONS"):
        needed = "admin" if path.startswith(ADMIN_READ_PREFIXES) else "viewer"
    elif path.startswith("/api/v1/collectors/") and path.endswith("/run"):
        needed = "operator"  # running a configured collector now
    else:
        needed = "admin" if path.startswith(ADMIN_WRITE_PREFIXES) or "/collectors" in path \
            or path.endswith(("/control", "/abort")) else "operator"
    if not principal.can(needed):
        raise HTTPException(403, f"Your role ({principal.role}) cannot do this; {needed} required")
    return principal


def require(role: str):
    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.can(role):
            raise HTTPException(403, f"Your role ({principal.role}) cannot do this; {role} required")
        return principal
    return dependency


# ------------------------------------------------------------------------------- audit
def audit(username: str, action: str, target: str | None, status_code: int | None,
          detail: str | None = None, ip: str | None = None) -> None:
    with get_db() as connection:
        connection.execute(audit_log.insert().values(
            username=username[:64], action=action[:80], target=(target or "")[:300] or None, status=status_code,
            detail=(detail or "")[:2000] or None, ip=(ip or "")[:64] or None))


def list_audit(limit: int = 200, username: str | None = None, action: str | None = None) -> list[dict]:
    query = select(audit_log).order_by(audit_log.c.at.desc(), audit_log.c.id.desc()).limit(limit)
    if username:
        query = query.where(audit_log.c.username == username)
    if action:
        query = query.where(audit_log.c.action.like(f"{action}%"))
    with get_db() as connection:
        rows = connection.execute(query).mappings().all()
    result = []
    for r in rows:
        item = dict(r)
        if hasattr(item["at"], "isoformat"):
            item["at"] = item["at"].isoformat()
        result.append(item)
    return result


def prune_audit() -> None:
    with get_db() as connection:
        connection.execute(delete(audit_log).where(
            audit_log.c.at < datetime.now(timezone.utc) - timedelta(days=config.AUDIT_RETENTION_DAYS)))
