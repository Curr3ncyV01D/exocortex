from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

# Корень проекта
BASE_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    # Путь к SQLite по умолчанию: корень_проекта/data/exocortex.db
    DATABASE_URL: str = f"sqlite+aiosqlite:///{BASE_DIR}/data/exocortex.db"
    
    # Ключ Gemini API
    GEMINI_API_KEY: str = ""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()