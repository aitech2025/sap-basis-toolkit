"""Sign-in, first-run setup, account, user administration and audit log."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import auth
from ..auth import Principal, current_principal, require

public = APIRouter(prefix="/api/v1/auth", tags=["auth"])
router = APIRouter(prefix="/api/v1", tags=["users"])


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class SetupIn(BaseModel):
    api_key: str = Field(min_length=1)
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    display_name: str | None = Field(default=None, max_length=120)


@public.get("/status")
def auth_status():
    """Public: whether first-run setup is still required."""
    return {"setup_required": auth.user_count() == 0}


@public.post("/setup", status_code=201)
def first_run_setup(payload: SetupIn, request: Request):
    """Create the first admin. Only possible while no users exist, and only with the server API key."""
    import secrets as _secrets

    from .. import config
    if auth.user_count() > 0:
        raise HTTPException(409, "Setup already completed; sign in instead")
    if not config.API_AUTH_TOKEN or not _secrets.compare_digest(payload.api_key, config.API_AUTH_TOKEN):
        raise HTTPException(401, "Invalid API key")
    user = auth.create_user(payload.username, payload.password, "admin", payload.display_name or "Administrator")
    auth.audit(user["username"], "setup.first_admin", user["username"], 201, None, client_ip(request))
    return user


@public.post("/login")
def login(payload: LoginIn, request: Request):
    user = auth.authenticate_password(payload.username, payload.password, client_ip(request))
    token, expires = auth.issue_token(user)
    return {"token": token, "expires_at": expires.isoformat(), "user": auth._public(user)}


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


@router.get("/me")
def me(principal: Principal = Depends(current_principal)):
    if principal.kind == "apikey":
        return {"username": "api-key", "role": "admin", "kind": "apikey", "display_name": "API key (service account)"}
    user = auth.get_user(principal.username)
    return {**auth._public(user), "kind": "user"}


@router.post("/me/password")
def change_own_password(payload: PasswordChangeIn, request: Request, principal: Principal = Depends(current_principal)):
    if principal.kind != "user":
        raise HTTPException(400, "The API key has no password")
    user = auth.get_user(principal.username)
    if not auth.verify_password(payload.current_password, user["password_hash"]):
        raise HTTPException(401, "Current password is incorrect")
    updated = auth.update_user(user["id"], {"password": payload.new_password}, Principal(user["username"], "admin", "user"))
    token, expires = auth.issue_token(auth.get_user(principal.username))
    return {"user": updated, "token": token, "expires_at": expires.isoformat()}


class UserIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    role: str = Field(pattern="^(viewer|operator|admin)$")
    display_name: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=254)


class UserPatch(BaseModel):
    role: str | None = Field(default=None, pattern="^(viewer|operator|admin)$")
    active: bool | None = None
    display_name: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=254)
    password: str | None = Field(default=None, max_length=256)


@router.get("/users")
def list_users(_: Principal = Depends(require("admin"))):
    return auth.list_users()


@router.post("/users", status_code=201)
def create_user(payload: UserIn, _: Principal = Depends(require("admin"))):
    return auth.create_user(payload.username, payload.password, payload.role, payload.display_name, payload.email)


@router.patch("/users/{user_id}")
def update_user(user_id: int, payload: UserPatch, principal: Principal = Depends(require("admin"))):
    return auth.update_user(user_id, payload.model_dump(exclude_unset=True), principal)


@router.get("/audit")
def audit_log(limit: int = Query(200, ge=1, le=2000), username: str | None = None, action: str | None = None,
              _: Principal = Depends(require("admin"))):
    return auth.list_audit(limit, username, action)
