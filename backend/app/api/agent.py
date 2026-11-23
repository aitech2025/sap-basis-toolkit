"""ABAP push agent API: token management (admin), ABAP sources download, and the ingest endpoint (agent token only)."""
import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response

from ..auth import authorize, require
from ..services import agent

router = APIRouter(prefix="/api/v1", dependencies=[Depends(authorize)])
ingest_router = APIRouter(prefix="/api/v1/agent")


def _errors(fn, *args):
    try:
        return fn(*args)
    except agent.AgentError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/systems/{system_id}/agent")
def agent_status(system_id: int):
    tokens = _errors(agent.list_tokens, system_id)
    active = next((t for t in tokens if not t["revoked_at"]), None)
    return {"active": active, "tokens": tokens, "endpoint": "/api/v1/agent/ingest",
            "abap_objects": ["ZCL_SENTINEL_AGENT", "Z_SENTINEL_PUSH"], "schema": 1}


def _abap_dir() -> Path | None:
    from .. import config
    for candidate in (config.ABAP_SOURCES_DIR, "/app/abap", str(Path(__file__).resolve().parents[3] / "abap")):
        if candidate and (Path(candidate) / "src").is_dir():
            return Path(candidate)
    return None


@router.get("/agent/abap-sources.zip")
def agent_abap_sources():
    """abapGit package of the push agent (ZCL_SENTINEL_AGENT, Z_SENTINEL_PUSH): import with abapGit 'offline' (ZIP)."""
    root = _abap_dir()
    if root is None:
        raise HTTPException(404, "ABAP sources are not part of this deployment; take them from the repository folder abap/")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.name != "abaplint.json":
                zf.write(path, path.relative_to(root).as_posix())
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="sentinel-abap-agent.zip"'})


@router.post("/systems/{system_id}/agent/token", status_code=201, dependencies=[Depends(require("admin"))])
def agent_issue_token(system_id: int, request: Request):
    """Issue a new agent token (shown once) and revoke the previous one."""
    return _errors(agent.issue_token, system_id, request.state.principal.username)


@router.delete("/systems/{system_id}/agent/token/{token_id}", status_code=204, dependencies=[Depends(require("admin"))])
def agent_revoke_token(system_id: int, token_id: int):
    _errors(agent.revoke, system_id, token_id)


@ingest_router.post("/ingest")
async def agent_ingest(request: Request, x_sentinel_agent_token: str | None = Header(default=None)):
    """Called by report Z_SENTINEL_PUSH in the SAP system. Authenticated by the system's agent token only."""
    body = await request.body()
    try:
        token_row = agent.authenticate(x_sentinel_agent_token)
        return agent.ingest(token_row, body, request.client.host if request.client else None)
    except agent.AgentError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
