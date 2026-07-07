from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import db
from .routers import calls


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    try:
        yield
    finally:
        await db.disconnect()


app = FastAPI(title="CQ v3 AI API", version="0.1.0", lifespan=lifespan)
app.include_router(calls.router)


@app.get("/health")
async def health():
    try:
        async with db.pool().acquire() as conn:
            await conn.fetchval("SELECT 1")
        return {"status": "ok", "database": "connected"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "database": "unavailable", "detail": str(exc)}
