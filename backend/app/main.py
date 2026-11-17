from contextlib import asynccontextmanager
from fastapi import FastAPI
from .api.routes import router
from .config import APP_NAME
from .db import init_db
from .services.scheduler import start_scheduler, stop_scheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(); start_scheduler()
    try: yield
    finally: stop_scheduler()

app = FastAPI(title=APP_NAME, description="SAP landscape certificate inventory, expiry monitoring and alerting.", version="0.2.0", lifespan=lifespan)
app.include_router(router)

@app.get("/health")
def health(): return {"status": "ok", "service": APP_NAME}
