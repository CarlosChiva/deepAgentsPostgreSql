from pydantic import Field
from pydantic_settings import BaseSettings

# ── Validation constants ────────────────────────────────────────────────

VALID_USER_ID_PATTERN: str = r"^[a-zA-Z0-9_-]+$"
"""Regex for valid user IDs: alphanumeric, underscore, hyphen only."""

MAX_USER_ID_LENGTH: int = 53
"""Maximum user ID length (63 max PG DB name minus 10 for "deepagent_" prefix)."""

USER_ID_BLOCKLIST: set[str] = {"postgres", "template0", "template1"}
"""Reserved PostgreSQL database names that may not be used as user IDs."""

USER_ID_BLOCKLIST_PREFIXES: tuple[str, ...] = ("pg_",)
"""Prefixes that user IDs may not start with (PostgreSQL internal naming)."""

DEFAULT_TENANT_TTL: int = 3600
"""Default tenant cache TTL in seconds."""

MAX_TENANT_CACHE_SIZE: int = 1000
"""Maximum number of cached tenant sessions."""


class Settings(BaseSettings):
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5440
    POSTGRES_DB: str = "chatdb"
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "devpass"
    TENANT_PREFIX: str = "deepagent_"
    TENANT_SUPERUSER_DB: str = "postgres"
    TENANT_DEFAULT_TTL_SECONDS: int = 3600
    TENANT_ENFORCE_USER_ID: bool = True
    TENANT_MAX_CACHE_SIZE: int = 1000
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    CHATOLLAMA_BASE_URL: str = "http://localhost:11434"
    LLM_MODEL: str = "qwen3.5:4b"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    CHAT_SERVICE_PORT: int = 8000
    MAX_MESSAGES_PER_THREAD: int = 100

    @property
    def postgres_uri(self) -> str:
        """Plain postgresql:// URI for LangGraph (psycopg3 direct)."""
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}"
        )

    model_config = {
        "extra": "ignore",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }


settings = Settings()
