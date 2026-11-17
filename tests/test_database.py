import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import event, inspect
from sqlalchemy.ext.asyncio import create_async_engine


def run_database_command(database: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, '-m', *args],
        env={**os.environ, 'DATABASE_URL': f'sqlite+aiosqlite:///{database.as_posix()}', 'PYTHONIOENCODING': 'utf-8'},
        capture_output=True, encoding='utf-8', timeout=30, check=False,
    )


def test_alembic_installs_the_schema_and_fts_triggers(tmp_path: Path) -> None:
    database = tmp_path / 'fresh.db'
    for args in [('upgrade', 'head'), ('upgrade', 'head'), ('check',)]:
        result = run_database_command(database, 'alembic', *args)
        assert result.returncode == 0, result.stderr

    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute('SELECT version_num FROM alembic_version').fetchone() == ('0001',)
        conn.execute("INSERT INTO chats VALUES ('chat', 'Chat', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
        conn.execute("INSERT INTO messages VALUES ('root', 'chat', NULL, 'USER', 'original', 0, 1, NULL, CURRENT_TIMESTAMP, NULL)")
        match = 'SELECT count(*) FROM messages_fts WHERE messages_fts MATCH ?'
        assert conn.execute(match, ('original',)).fetchone() == (1,)
        conn.execute("UPDATE messages SET content = 'updated' WHERE id = 'root'")
        assert conn.execute(match, ('original',)).fetchone() == (0,)
        assert conn.execute(match, ('updated',)).fetchone() == (1,)
        conn.execute("DELETE FROM messages WHERE id = 'root'")
        assert conn.execute(match, ('updated',)).fetchone() == (0,)


def test_adopt_legacy_database_preserves_data_and_backs_up_before_stamp(tmp_path: Path) -> None:
    database = tmp_path / 'legacy.db'
    created = run_database_command(database, 'alembic', 'upgrade', 'head')
    assert created.returncode == 0, created.stderr
    with closing(sqlite3.connect(database)) as conn:
        conn.execute('DROP TABLE alembic_version')
        conn.execute("INSERT INTO chats VALUES ('chat', 'Imported', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
        conn.execute("INSERT INTO messages VALUES ('root', 'chat', NULL, 'USER', 'archive', 0, 1, NULL, CURRENT_TIMESTAMP, NULL)")
        conn.execute("INSERT INTO branches VALUES ('main-id', 'chat', 'main', 'root', NULL, NULL, 'ACTIVE', CURRENT_TIMESTAMP)")
        conn.commit()

    adopted = run_database_command(database, 'scripts.adopt_legacy_db')
    assert adopted.returncode == 0, adopted.stderr
    (backup,) = tmp_path.glob('legacy.db.bak_*')
    assert backup.name in adopted.stdout
    with closing(sqlite3.connect(backup)) as conn:
        assert conn.execute("SELECT name FROM sqlite_schema WHERE name = 'alembic_version'").fetchone() is None
        assert conn.execute('SELECT id FROM chats').fetchone() == ('chat',)
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute('SELECT head_message_id FROM branches').fetchone() == ('root',)
        assert conn.execute("SELECT count(*) FROM messages_fts WHERE messages_fts MATCH 'archive'").fetchone() == (1,)
        assert conn.execute('SELECT version_num FROM alembic_version').fetchone() == ('0001',)


def test_adoption_refuses_an_incompatible_schema(tmp_path: Path) -> None:
    database = tmp_path / 'outdated.db'
    created = run_database_command(database, 'alembic', 'upgrade', 'head')
    assert created.returncode == 0, created.stderr
    with closing(sqlite3.connect(database)) as conn:
        conn.execute('DROP TABLE alembic_version')
        conn.execute('DROP TRIGGER messages_au')
        conn.commit()

    adopted = run_database_command(database, 'scripts.adopt_legacy_db')
    assert adopted.returncode != 0
    assert 'messages_au' in adopted.stderr
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("SELECT name FROM sqlite_schema WHERE name = 'alembic_version'").fetchone() is None


@pytest.mark.asyncio
async def test_schema_upgrade_rolls_back_ddl_and_revision_after_partial_failure() -> None:
    from src.storage.migrations import require_current_schema, upgrade_schema

    engine = create_async_engine('sqlite+aiosqlite:///:memory:')

    def fail_trigger(conn, cursor, statement, parameters, context, executemany):
        if 'CREATE TRIGGER messages_ad' in statement:
            raise RuntimeError('injected DDL failure')

    try:
        with pytest.raises(RuntimeError, match='alembic upgrade head'):
            await require_current_schema(engine)
        event.listen(engine.sync_engine, 'before_cursor_execute', fail_trigger)
        with pytest.raises(RuntimeError, match='injected DDL failure'):
            await upgrade_schema(engine)
        event.remove(engine.sync_engine, 'before_cursor_execute', fail_trigger)
        async with engine.connect() as conn:
            assert await conn.run_sync(lambda sync: inspect(sync).get_table_names()) == []
        await upgrade_schema(engine)
        await require_current_schema(engine)
    finally:
        await engine.dispose()
