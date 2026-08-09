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
    # 限流的两个上限 (#27)。默认值是「挡住失控的重试和脚本,挡不住正常使用」
    # 的量级,不是精算出来的——真实数字要看代理的账单,而账单还没有。
    ai_calls_per_hour: int = 30
    login_attempts_per_15_minutes: int = 20
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

    # Non-secret Compose-only overrides (BACKEND_PORT, FRONTEND_PORT) live in
    # the same .env file this reads directly outside Docker; they are not
    # settings this app has, so extra keys here must not be an error.
    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
