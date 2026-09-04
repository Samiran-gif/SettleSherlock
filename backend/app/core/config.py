"""Application configuration, loaded from environment variables / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings for the SettleSherlock backend."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Project metadata
    PROJECT_NAME: str = "SettleSherlock"
    PROJECT_DESCRIPTION: str = (
        "AI-powered settlement investigation system (PS-8)."
    )
    VERSION: str = "0.1.0"

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = True

    # API
    API_V1_PREFIX: str = "/api/v1"

    # Comma-separated list of allowed CORS origins
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    # AI explanation layer.
    # Any OpenAI-compatible chat-completions provider works; the defaults point
    # at Groq's free tier. The key is read from the environment only and must
    # never be committed. With no key set, the API falls back to a
    # deterministic summary instead of failing.
    AI_ENABLED: bool = True
    AI_API_KEY: str = ""
    AI_BASE_URL: str = "https://api.groq.com/openai/v1"
    AI_MODEL: str = "llama-3.3-70b-versatile"
    AI_TIMEOUT_SECONDS: float = 15.0
    AI_MAX_TOKENS: int = 320

    @property
    def ai_configured(self) -> bool:
        """True when the AI layer is switched on and has a key to use."""
        return bool(self.AI_ENABLED and self.AI_API_KEY.strip())

    @property
    def cors_origins_list(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.CORS_ORIGINS.split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor, usable as a FastAPI dependency."""
    return Settings()


settings = get_settings()
