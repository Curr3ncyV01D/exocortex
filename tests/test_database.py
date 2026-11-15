import os
import subprocess
import sys

import pytest
from sqlalchemy import event, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from src.storage.database import Base, init_db


def test_init_db_can_run_before_models_are_imported() -> None:
    program = """
import asyncio
from src.storage.database import engine, init_db

async def main():
    try:
        await init_db()
    finally:
        await engine.dispose()

asyncio.run(main())
"""
    subprocess.run(
        [sys.executable, "-c", program],
        check=True,
        timeout=20,
        env={**os.environ, "DATABASE_URL": "sqlite+aiosqlite:///:memory:"},
    )


@pytest.mark.asyncio
async def test_init_db_backfills_existing_messages_and_can_repeat() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            legacy_tables = [table for table in Base.metadata.sorted_tables if table.name != "message_submissions"]
            await conn.run_sync(Base.metadata.create_all, tables=legacy_tables)
            await conn.execute(text(
                "INSERT INTO chats(id, title, created_at, updated_at) "
                "VALUES ('chat', 'Chat', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ))
            await conn.execute(text(
                "INSERT INTO messages(id, chat_id, role, content, is_active, is_edited, created_at) "
                "VALUES ('root', 'chat', 'USER', 'previous text', 1, 0, CURRENT_TIMESTAMP)"
            ))
        await init_db(engine)
        await init_db(engine)
        async with engine.begin() as conn:
            assert "message_submissions" in await conn.run_sync(lambda sync: inspect(sync).get_table_names())
            matches = text("SELECT count(*) FROM messages_fts WHERE messages_fts MATCH 'previous'")
            assert await conn.scalar(matches) == 1
            await conn.execute(text("UPDATE messages SET content = 'updated text' WHERE id = 'root'"))
            assert await conn.scalar(matches) == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_init_db_rolls_back_schema_after_partial_failure() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    def fail_trigger(conn, cursor, statement, parameters, context, executemany):
        if "CREATE TRIGGER IF NOT EXISTS messages_ad" in statement:
            raise RuntimeError("injected DDL failure")

    try:
        event.listen(engine.sync_engine, "before_cursor_execute", fail_trigger)
        with pytest.raises(RuntimeError, match="injected DDL failure"):
            await init_db(engine)
        event.remove(engine.sync_engine, "before_cursor_execute", fail_trigger)
        async with engine.connect() as conn:
            assert await conn.run_sync(lambda sync: inspect(sync).get_table_names()) == []
        await init_db(engine)
        async with engine.connect() as conn:
            assert "messages_fts" in await conn.run_sync(lambda sync: inspect(sync).get_table_names())
    finally:
        await engine.dispose()
