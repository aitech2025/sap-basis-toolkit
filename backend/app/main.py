from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from . import config
from .api.routes import router
from .api.toolkit import router as toolkit_router
from .config import APP_NAME
from .db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title=APP_NAME,
    description=(
        "Multi-system SAP Basis toolkit for ECC, S/4HANA, BW, NetWeaver ABAP/Java and more: certificate "
        "monitoring, table growth watchdog, user & license optimizer and transport checker."
    ),
    version="0.5.0",
    lifespan=lifespan,
)
app.include_router(router)
app.include_router(toolkit_router)


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
