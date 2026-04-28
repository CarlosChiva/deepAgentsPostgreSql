from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5440
    POSTGRES_DB: str = "chatdb"
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    CHATOLLAMA_BASE_URL: str = "http://localhost:11434"
    LLM_MODEL: str = "qwen3.5:4b"
    APP_HOST: str = "0.0.0.0"
    CHAT_SERVICE_PORT: int = 8000
    MAX_MESSAGES_PER_THREAD: int = 100

    @property
    def postgres_url(self) -> str:
        """Build a postgresql:// connection string."""
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    model_config = {"extra": "ignore"}


settings = Settings()
