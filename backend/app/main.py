from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

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
    description="SAP Basis toolkit: certificate monitoring, table growth watchdog, user & license optimizer and transport checker.",
    version="0.4.0",
    lifespan=lifespan,
)
app.include_router(router)
app.include_router(toolkit_router)


@app.get("/health")
def health():
    return {"status": "ok", "service": APP_NAME}


@app.get("/", include_in_schema=False)
def dashboard():
    """Static dashboard; all data calls require the API key."""
    return FileResponse(Path(__file__).parent / "static" / "index.html")
