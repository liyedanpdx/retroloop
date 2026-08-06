from datetime import datetime, timezone

from beanie import Document, Indexed
from pydantic import EmailStr


class User(Document):
    email: Indexed(EmailStr, unique=True)
    hashed_password: str
    display_name: str
    created_at: datetime = datetime.now(timezone.utc)

    class Settings:
        name = "users"
