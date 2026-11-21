from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Корень проекта
BASE_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    # Путь к SQLite по умолчанию: корень_проекта/data/exocortex.db
    DATABASE_URL: str = f"sqlite+aiosqlite:///{BASE_DIR}/data/exocortex.db"
    
    # Ключ Gemini API
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.5-flash-lite"
    MAX_CONTEXT_BYTES: int = Field(default=512_000, gt=0)
    GEMINI_REQUEST_TIMEOUT_SECONDS: float = Field(default=300, gt=0)
    AGENT_OPERATION_TIMEOUT_SECONDS: float = Field(default=1800, gt=0)

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
