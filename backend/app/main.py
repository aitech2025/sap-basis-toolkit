from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from . import auth, config
from .api.auth_routes import public as auth_public_router
from .api.auth_routes import router as auth_router
from .api.lifecycle import router as lifecycle_router
from .api.monitoring import router as monitoring_router
from .api.routes import router
from .api.toolkit import router as toolkit_router
from .config import APP_NAME
from .db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    auth.bootstrap_admin()
    from .services import incidents
    incidents.seed_builtin_rules()
    from .services import control
    control.mark_interrupted()
    yield


app = FastAPI(
    title=APP_NAME,
    description=(
        "Multi-system SAP Basis toolkit for ECC, S/4HANA, BW, NetWeaver ABAP/Java and more: certificate "
        "monitoring, table growth watchdog, user & license optimizer and transport checker."
    ),
    version="1.1.0",
    lifespan=lifespan,
)
app.include_router(auth_public_router)
app.include_router(auth_router)
app.include_router(router)
app.include_router(toolkit_router)
app.include_router(monitoring_router)
app.include_router(lifecycle_router)


@app.middleware("http")
async def audit_mutations(request: Request, call_next):
    """Record every state-changing API call with the acting user (login is audited separately)."""
    response = await call_next(request)
    path = request.url.path
    if (request.method in ("POST", "PUT", "PATCH", "DELETE") and path.startswith("/api/")
            and not path.startswith("/api/v1/auth/")):
        principal = getattr(request.state, "principal", None)
        try:
            auth.audit(principal.username if principal else "anonymous", f"{request.method} {path}"[:80],
                       path, response.status_code, request.url.query or None,
                       request.client.host if request.client else None)
        except Exception:  # auditing must never break the request
            pass
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


@app.get("/health")
def health():
    return {"status": "ok", "service": APP_NAME}


def _dist() -> Path:
    return Path(config.FRONTEND_DIST)


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    """Serve the built React UI (static assets + client-side routes). Data calls need the API key."""
    if path.startswith(("api/", "health")):
        raise HTTPException(404, "Not found")
    dist = _dist().resolve()
    index = dist / "index.html"
    if not index.is_file():
        return JSONResponse(
            {"detail": "UI not built. Run `npm run build` in frontend/ or use the Docker image. API docs: /docs"},
            status_code=404,
        )
    if path:
        candidate = (dist / path).resolve()
        if candidate.is_file() and dist in candidate.parents:
            return FileResponse(candidate)
        if "." in path.rsplit("/", 1)[-1]:
            raise HTTPException(404, "Not found")
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
