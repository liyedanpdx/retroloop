from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie

from app.config import settings
from app.models.cycle import Cycle
from app.models.feedback import FeedbackCard
from app.models.project import Project
from app.models.retro import Retrospective
from app.models.user import User

DOCUMENT_MODELS: list = [User, Project, Cycle, FeedbackCard, Retrospective]


async def init_db() -> None:
    client = AsyncIOMotorClient(settings.mongo_url)
    await init_beanie(
        database=client[settings.mongo_db_name],
        document_models=DOCUMENT_MODELS,
    )
