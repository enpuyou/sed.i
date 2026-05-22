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
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours
    REFRESH_TOKEN_EXPIRE_DAYS: int = 90
    MCP_REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    OPENAI_API_KEY: str = ""
    DISCOGS_TOKEN: str = ""
    DEBUG: bool = True

    # Feature Flags / Tuning
    SIMILARITY_THRESHOLD_CONNECTIONS: float = 0.3
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

    # LLM provider: "openai" | "bedrock"
    # Bedrock implementation lives in app/core/llm_client.py — swap here, no call-site changes.
    LLM_PROVIDER: str = "openai"

    # AWS / Bedrock (Layer 4)
    # Required when LLM_PROVIDER="bedrock". Leave empty when using OpenAI.
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"
    # Model IDs — override to pin a specific version
    BEDROCK_FAST_MODEL: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    BEDROCK_SMART_MODEL: str = "us.anthropic.claude-sonnet-4-5-20251001-v1:0"

    # S3 object storage (Layer 6)
    # Leave empty to disable S3 upload (PDFs processed in-memory only, bytes discarded).
    AWS_S3_BUCKET: str = ""
    # Presigned URL expiry in seconds (default 1 hour)
    AWS_S3_PRESIGN_EXPIRY: int = 3600

    # Prefect pipeline observability (Layer 8)
    # When true, Phase 2+ of ingestion runs as a Prefect flow instead of
    # the Celery fire-and-forget chain. False by default — existing behavior unchanged.
    PREFECT_ENABLED: bool = False

    # Braintrust — LLM observability (Layer 1)
    # Leave empty to disable tracing (safe in dev/test without an account).
    BRAINTRUST_API_KEY: str = ""

    # Sentry — error tracking (Layer 2)
    SENTRY_DSN: str = ""

    # OpenTelemetry — infra tracing (Layer 2)
    # OTLP endpoint for trace export (e.g. Grafana Cloud OTLP URL).
    # Leave empty to use console exporter for local inspection.
    OTEL_EXPORTER_OTLP_ENDPOINT: str = ""
    OTEL_SERVICE_NAME: str = "sedi-backend"

    model_config = SettingsConfigDict(env_file=_env_file, extra="ignore")


settings = Settings()
