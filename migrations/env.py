import asyncio
from collections.abc import Mapping

from alembic import context
from sqlalchemy.engine import Connection

from src.storage import models  # Register tables in Base.metadata.
from src.storage.database import Base, engine


def include_name(name: str | None, type_: str, _parent_names: Mapping[str, str | None]) -> bool:
    """Keep explicitly managed FTS5 objects out of relational autogeneration."""
    return not (type_ == "table" and name and (name == "messages_fts" or name.startswith("messages_fts_")))


def run_online(connection: Connection) -> None:
    def migrate() -> None:
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            include_name=include_name,
            render_as_batch=True,
            transactional_ddl=True,
        )
        with context.begin_transaction():
            context.run_migrations()

    if connection.in_transaction():
        migrate()
    else:
        with connection.begin():
            # SQLite legacy transaction control does not begin a transaction for DDL.
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            migrate()


async def run_async() -> None:
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_online)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    context.configure(dialect_name="sqlite", literal_binds=True, transactional_ddl=True)
    with context.begin_transaction():
        context.run_migrations()
elif (connection := context.config.attributes.get("connection")) is not None:
    run_online(connection)
else:
    asyncio.run(run_async())
