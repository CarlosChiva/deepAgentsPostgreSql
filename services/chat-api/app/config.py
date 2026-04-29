from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5440
    POSTGRES_DB: str = "chatdb"
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    TENANT_PREFIX: str = "deepagent_"
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