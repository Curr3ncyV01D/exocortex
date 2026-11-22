"""Validate and register a pre-Alembic ExoCortex SQLite database."""

import asyncio
import sqlite3
import sys
from pathlib import Path

from alembic import command
from alembic.util import CommandError
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from scripts.migrate_ai_studio import backup_database
from src.storage.database import engine
from src.storage.migrations import migration_config

BASELINE = "0001"


def _schema_objects(connection: Connection) -> dict[tuple[str, str], str]:
    rows = connection.exec_driver_sql("""
        SELECT type, name, sql FROM sqlite_schema
        WHERE sql IS NOT NULL AND name NOT GLOB 'sqlite_*'
          AND name NOT GLOB 'messages_fts_*' AND name <> 'alembic_version'
    """)
    return {(kind, name): " ".join(ddl.split()) for kind, name, ddl in rows}


def _stamp_legacy_schema(connection: Connection) -> None:
    reference = create_engine("sqlite://")
    try:
        with reference.connect() as expected_connection:
            command.upgrade(migration_config(expected_connection), BASELINE)
            expected = _schema_objects(expected_connection)
    finally:
        reference.dispose()

    with connection.begin():
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        if connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_schema WHERE name = 'alembic_version'"
        ).first() is not None:
            raise ValueError("База уже использует Alembic. Запустите python -m alembic upgrade head.")

        actual = _schema_objects(connection)
        different = sorted(
            name for kind, name in actual.keys() | expected.keys()
            if actual.get((kind, name)) != expected.get((kind, name))
        )
        if different:
            raise ValueError(f"Схема отличается от ревизии {BASELINE}. Объекты: {', '.join(different)}.")
        if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
            raise ValueError("В базе найдены нарушенные внешние ключи.")
        connection.exec_driver_sql("INSERT INTO messages_fts(messages_fts, rank) VALUES ('integrity-check', 1)")
        command.stamp(migration_config(connection), BASELINE)


async def _adopt() -> None:
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_stamp_legacy_schema)
    finally:
        await engine.dispose()


def main() -> int:
    url = engine.url
    try:
        if url.drivername != "sqlite+aiosqlite" or not url.database or url.database == ":memory:" or "uri" in url.query:
            raise ValueError("DATABASE_URL должен указывать на существующий файл SQLite.")
        database = Path(url.database).resolve()
        backup = backup_database(database)
        if backup is None:
            raise ValueError("Файл базы не существует. Для новой базы выполните python -m alembic upgrade head.")
        print(f"Резервная копия: {backup}", flush=True)
        asyncio.run(_adopt())
    except (OSError, ValueError, sqlite3.Error, SQLAlchemyError, CommandError) as error:
        print(f"Не удалось подключить базу к Alembic: {error}", file=sys.stderr)
        return 1
    print(f"База подключена к Alembic на ревизии {BASELINE}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
