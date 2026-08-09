from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie

from app.config import settings
from app.models.cycle import Cycle
from app.models.feedback import FeedbackCard
from app.models.project import Project
from app.models.retro import Retrospective
from app.models.user import User

DOCUMENT_MODELS: list = [User, Project, Cycle, FeedbackCard, Retrospective]

# The client the app initialised, kept so readiness can ping the same connection
# the request path uses rather than opening a second one and proving nothing.
_client: AsyncIOMotorClient | None = None


async def init_db() -> None:
    global _client
    _client = AsyncIOMotorClient(settings.mongo_url)
    await init_beanie(
        database=_client[settings.mongo_db_name],
        document_models=DOCUMENT_MODELS,
    )


async def database_is_reachable() -> bool:
    """One `ping`, and nothing else (#18).

    Read-only by design: readiness must never create, seed or touch a
    collection, because it runs against whatever database the team configured
    and `_docs/decisions.md` keeps that one external and valued.
    """
    if _client is None:
        return False
    try:
        await _client.admin.command("ping")
    except Exception:
        # The reason belongs in the container log, not in a 503 body — a driver
        # error message carries the connection string.
        return False
    return True
