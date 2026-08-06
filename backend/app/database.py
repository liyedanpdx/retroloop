from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie

from app.config import settings
from app.models.project import Project
from app.models.user import User

DOCUMENT_MODELS: list = [User, Project]


async def init_db() -> None:
    client = AsyncIOMotorClient(settings.mongo_url)
    await init_beanie(
        database=client[settings.mongo_db_name],
        document_models=DOCUMENT_MODELS,
    )
