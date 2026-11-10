from collections.abc import AsyncGenerator
from pathlib import Path
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from src.config import settings

# Автоматически создаем директорию data/, если база локальная
if not settings.DATABASE_URL.endswith(":memory:"):
    db_file_path = settings.DATABASE_URL.replace("sqlite+aiosqlite:///", "")
    Path(db_file_path).parent.mkdir(parents=True, exist_ok=True)

# 1. Создаем асинхронный движок
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
)

# 2. Инжектируем обязательные PRAGMA-директивы из SPEC.md (Раздел 2.3)
@event.listens_for(engine.sync_engine, "connect")
def configure_sqlite_pragmas(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode = WAL;")
    cursor.execute("PRAGMA foreign_keys = ON;")
    cursor.execute("PRAGMA synchronous = NORMAL;")
    cursor.execute("PRAGMA cache_size = -64000;")
    cursor.close()

# 3. Фабрика асинхронных сессий
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

# 4. Базовый декларативный класс для всех будущих моделей
class Base(AsyncAttrs, DeclarativeBase):
    pass

# 5. Dependency для FastAPI (получение сессии на запрос)
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session