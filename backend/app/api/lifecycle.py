"""Discovery (host + credentials -> registered, monitored system) and start/stop/restart control."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import authorize
from ..services import control, discovery
from ..services.systems import SystemNotFound
from .toolkit import load_system

router = APIRouter(prefix="/api/v1", dependencies=[Depends(authorize)])


class ScanIn(BaseModel):
    method: str = Field(default="sapcontrol", pattern="^(sapcontrol|os)$")
    host: str = Field(min_length=1, max_length=253)
    # OS login (method=os): root or <sid>adm over SSH; only fixed read-only commands are run
    os_user: str | None = Field(default=None, max_length=32)
    os_password: str | None = Field(default=None, max_length=512)
    os_private_key: str | None = Field(default=None, max_length=16384)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    expected_host_key: str | None = Field(default=None, max_length=120)
    sid: str | None = Field(default=None, max_length=3)
    sap_user: str | None = Field(default=None, max_length=120)
    sap_password: str | None = Field(default=None, max_length=512)
    db_os_user: str | None = Field(default=None, max_length=32)
    db_os_password: str | None = Field(default=None, max_length=512)
    db_os_private_key: str | None = Field(default=None, max_length=16384)
    expected_db_host_key: str | None = Field(default=None, max_length=120)
    username: str | None = Field(default=None, max_length=120)
    password: str | None = Field(default=None, max_length=512)
    use_tls: bool = False
    verify_tls: bool = True
    instance_nr: str | None = Field(default=None, max_length=2)


class HanaIn(BaseModel):
    host: str | None = Field(default=None, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=120)
    password: str | None = Field(default=None, max_length=512)
    use_tls: bool = True
    verify_tls: bool = True
    database_name: str | None = Field(default=None, max_length=64)
    abap_schema: str | None = Field(default=None, max_length=64)


class DbControlIn(BaseModel):
    username: str | None = Field(default=None, max_length=120)
    password: str | None = Field(default=None, max_length=512)


class RegisterIn(ScanIn):
    expected_sid: str | None = Field(default=None, max_length=3)
    system_id: int | None = None
    name: str | None = Field(default=None, max_length=120)
    product: str | None = None
    product_version: str | None = Field(default=None, max_length=60)
    basis_release: str | None = Field(default=None, max_length=20)
    stack: str | None = None
    database: str | None = None
    environment: str | None = None
    landscape: str | None = Field(default=None, max_length=80)
    default_client: str | None = Field(default=None, max_length=3)
    timezone: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=2000)
    control_enabled: bool | None = None
    interval_seconds: int = Field(default=300, ge=30, le=86400)
    role_overrides: dict[str, str] = Field(default_factory=dict)
    hana: HanaIn | None = None
    db_control: DbControlIn | None = None
    start_monitoring: bool = True


def _discovery(fn, *args):
    try:
        return fn(*args)
    except discovery.DiscoveryError as exc:
        raise HTTPException(422, str(exc))
    except SystemNotFound as exc:
        raise HTTPException(404, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.post("/discovery/scan")
def scan(payload: ScanIn):
    """Probe a host's sapstartsrv ports and describe the SAP system found (nothing is stored)."""
    if payload.method == "os":
        return _discovery(discovery.os_scan, payload.model_dump())
    return _discovery(discovery.scan, payload.host, payload.username, payload.password, payload.use_tls,
                      payload.verify_tls, payload.instance_nr)


@router.post("/discovery/register", status_code=201)
def register(payload: RegisterIn):
    """Register (or update) the discovered system, its instances and collectors, and start monitoring."""
    data = payload.model_dump()
    if data.get("hana") and not data["hana"].get("host"):
        data["hana"] = None
    return _discovery(discovery.register, data)


@router.get("/systems/{system_id}/infrastructure")
def infrastructure(system_id: int):
    """Application, database and OS layers recorded by discovery."""
    load_system(system_id)
    return discovery.infrastructure(system_id)


@router.post("/systems/{system_id}/infrastructure/refresh")
def refresh_infrastructure(system_id: int):
    load_system(system_id)
    return _discovery(discovery.refresh_infrastructure, system_id)


@router.get("/systems/{system_id}/instances")
def instances(system_id: int):
    load_system(system_id)
    return discovery.list_instances(system_id)


class RoleIn(BaseModel):
    role: str = Field(min_length=2, max_length=20)


@router.patch("/systems/{system_id}/instances/{instance_id}")
def set_role(system_id: int, instance_id: int, payload: RoleIn):
    load_system(system_id)
    return _discovery(discovery.set_role, system_id, instance_id, payload.role)


@router.post("/systems/{system_id}/instances/refresh")
def refresh(system_id: int):
    load_system(system_id)
    return _discovery(discovery.refresh, system_id)


class ControlIn(BaseModel):
    action: str = Field(pattern="^(start|stop|restart)$")
    scope: str = Field(default="system", pattern="^(system|app|db|instance)$")
    hostname: str | None = Field(default=None, max_length=253)
    instance_nr: str | None = Field(default=None, max_length=2)
    soft_timeout: int | None = Field(default=None, ge=0, le=3600)
    confirm_sid: str = Field(min_length=3, max_length=3)
    reason: str = Field(min_length=1, max_length=500)
    maintenance_minutes: int | None = Field(default=None, ge=0, le=1440)
    checklist: dict[str, bool] = Field(default_factory=dict)
    force: bool = False
    wait: bool = False


def _control(fn, *args):
    try:
        return fn(*args)
    except control.ControlConflict as exc:
        raise HTTPException(409, str(exc))
    except control.ControlError as exc:
        raise HTTPException(422, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.get("/systems/{system_id}/control")
def control_status(system_id: int):
    load_system(system_id)
    return control.capabilities(system_id)


@router.get("/systems/{system_id}/control/plan")
def control_plan(system_id: int, request: Request, action: str, scope: str = "system",
                 hostname: str | None = None, instance_nr: str | None = None):
    """The ordered procedure with read-only pre-checks evaluated (admins only: it uses the stored credentials)."""
    if not request.state.principal.can("admin"):
        raise HTTPException(403, "Only admins can plan start/stop procedures")
    load_system(system_id)
    return _control(control.evaluate_plan, system_id, action, scope, hostname, instance_nr)


@router.post("/systems/{system_id}/control", status_code=202)
def control_request(system_id: int, payload: ControlIn, request: Request):
    load_system(system_id)
    return _control(control.request, system_id, payload.model_dump(), request.state.principal.username)


@router.post("/operations/{operation_id}/abort")
def abort_operation(operation_id: int, request: Request):
    return _control(control.abort, operation_id, request.state.principal.username)


class ConfirmIn(BaseModel):
    done: bool = True


@router.post("/operations/{operation_id}/steps/{step_key}/confirm")
def confirm_step(operation_id: int, step_key: str, payload: ConfirmIn, request: Request):
    return _control(control.confirm_step, operation_id, step_key, request.state.principal.username, payload.done)


@router.get("/systems/{system_id}/operations")
def operations(system_id: int, limit: int = 50):
    load_system(system_id)
    return control.list_operations(system_id, min(max(limit, 1), 200))


@router.get("/operations/{operation_id}")
def operation(operation_id: int):
    try:
        return control.get_operation(operation_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.get("/meta/instance-roles")
def roles():
    return [{"role": r, "label": discovery.ROLE_LABELS[r]} for r in discovery.ROLES]
