from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie

from app.config import settings

# Will be populated as models are added in later tasks
DOCUMENT_MODELS: list = []


async def init_db() -> None:
    client = AsyncIOMotorClient(settings.mongo_url)
    await init_beanie(
        database=client[settings.mongo_db_name],
        document_models=DOCUMENT_MODELS,
    )
