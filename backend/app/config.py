from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    mongo_url: str = "mongodb://localhost:27017"
    mongo_db_name: str = "retroloop"
    openai_base_url: str = "http://localhost:8080/v1"
    openai_api_key: str = ""
    jwt_secret: str = "change-me"

    model_config = {"env_file": ".env"}


settings = Settings()
