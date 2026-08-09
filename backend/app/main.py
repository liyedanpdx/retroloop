from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.clusters import router as clusters_router
from app.api.cycles import router as cycles_router
from app.api.discussion import router as discussion_router
from app.api.feedback import router as feedback_router
from app.api.projects import router as projects_router
from app.api.summary import router as summary_router
from app.api.retros import router as retros_router
from app.api.transcript import router as transcript_router
from app.api.votes import router as votes_router
from app.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="RetroLoop", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(projects_router)
app.include_router(cycles_router)
app.include_router(feedback_router)
app.include_router(retros_router)
app.include_router(clusters_router)
app.include_router(votes_router)
app.include_router(discussion_router)
app.include_router(transcript_router)
app.include_router(summary_router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
