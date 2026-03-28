import os
from pydantic_settings import BaseSettings, SettingsConfigDict

# Allow overriding the .env file path via ENV_FILE env var.
# Used by the MCP server launched from Claude Desktop, which doesn't
# inherit the shell's working directory where .env normally lives.
_env_file = os.environ.get(
    "ENV_FILE",
    os.path.join(os.path.dirname(__file__), "../../.env"),
)


class Settings(BaseSettings):
    DATABASE_URL: str
    REDIS_URL: str
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 10080
    OPENAI_API_KEY: str = ""
    DISCOGS_TOKEN: str = ""
    DEBUG: bool = True

    # Feature Flags / Tuning
    SIMILARITY_THRESHOLD_CONNECTIONS: float = 0.5
    SIMILARITY_THRESHOLD_TAGS: float = 0.75

    # Email Settings (Resend HTTP API)
    RESEND_API_KEY: str = ""
    EMAILS_FROM_EMAIL: str = "noreply@read-sedi.com"
    EMAILS_FROM_NAME: str = "sed.i Team"
    FRONTEND_URL: str = "http://localhost:3000"

    # Public-facing API base URL (used in OAuth discovery behind reverse proxies)
    # Set to e.g. https://api.read-sedi.com in production Railway env vars.
    API_BASE_URL: str = ""

    # PostHog Analytics
    POSTHOG_API_KEY: str = ""
    POSTHOG_HOST: str = "https://us.i.posthog.com"

    model_config = SettingsConfigDict(env_file=_env_file, extra="ignore")


settings = Settings()
