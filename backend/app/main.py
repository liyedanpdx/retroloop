from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.cycles import router as cycles_router
from app.api.projects import router as projects_router
from app.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="RetroLoop", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(projects_router)
app.include_router(cycles_router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
