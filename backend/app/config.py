from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    mongo_url: str = "mongodb://localhost:27017"
    mongo_db_name: str = "retroloop"
    openai_base_url: str = "http://localhost:8080/v1"
    openai_api_key: str = ""
    # The one place the proxy model name lives (`_docs/decisions.md`, "One model
    # name, one setting"). This class forbids extra keys, so OPENAI_MODEL in a
    # .env without this field is a startup failure rather than an ignored line.
    openai_model: str = "gpt-5.4"
    jwt_secret: str = "change-me"
    jwt_refresh_secret: str = "change-me-refresh"
    # The refresh cookie's deployment-dependent attributes (#30). The defaults
    # are the development ones: a `Secure` cookie is dropped by the browser over
    # plain HTTP, so localhost could not log in at all with `secure=True`.
    # Production sets COOKIE_SECURE=true next to its https origin.
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    # The single browser origin allowed to send credentialed requests (#30).
    # Never `*`: a wildcard cannot be combined with credentials, and a browser
    # that is sent both refuses the response outright.
    frontend_origin: str = "http://localhost:3000"

    model_config = {"env_file": ".env"}


settings = Settings()
