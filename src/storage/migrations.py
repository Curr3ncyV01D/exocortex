from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from src.config import BASE_DIR


def migration_config(connection: Connection | None = None) -> Config:
    """Build the Alembic configuration independently of the working directory."""
    config = Config(str(BASE_DIR / "alembic.ini"))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


async def upgrade_schema(engine: AsyncEngine) -> None:
    """Apply migrations using the existing engine, including in-memory test databases."""
    async with engine.connect() as connection:
        await connection.run_sync(lambda sync: command.upgrade(migration_config(sync), "head"))


async def require_current_schema(engine: AsyncEngine) -> None:
    """Verify the migration revision at startup without changing the schema."""
    expected = set(ScriptDirectory.from_config(migration_config()).get_heads())
    async with engine.connect() as connection:
        current = await connection.run_sync(lambda sync: MigrationContext.configure(sync).get_current_heads())
    if set(current) != expected:
        raise RuntimeError(
            "Схема БД не обновлена. Выполните python -m alembic upgrade head. "
            "Для существующей базы без ревизии сначала используйте python -m scripts.adopt_legacy_db."
        )
