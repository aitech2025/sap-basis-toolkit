from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.routes import router
from .config import APP_NAME
from .db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title=APP_NAME,
    description="Lightweight SAP landscape certificate inventory and expiry monitoring.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok", "service": APP_NAME}
