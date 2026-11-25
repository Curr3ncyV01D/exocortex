"""Verify additive provenance migration against actual revision-0001 SQLite files."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from tests.test_database import run_database_command


def test_upgrade_preserves_old_merge_history_and_leaves_provenance_unknown(tmp_path: Path) -> None:
    """Existing boundaries and masked messages survive without guessing an old source HEAD."""
    database = tmp_path / "history.db"
    result = run_database_command(database, "alembic", "upgrade", "0001")
    assert result.returncode == 0, result.stderr
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("INSERT INTO chats VALUES ('chat', 'Old', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
        for identity, parent, role, content, active in [
            ("root", None, "USER", "Root", 1), ("source", "root", "MODEL", "Hidden", 0),
            ("summary", "root", "SYSTEM_MERGE", "Oldsummary", 1),
        ]:
            conn.execute("INSERT INTO messages VALUES (?, 'chat', ?, ?, ?, 0, ?, NULL, CURRENT_TIMESTAMP, NULL)",
                         (identity, parent, role, content, active))
        conn.execute("INSERT INTO merge_events VALUES ('summary', 'chat', 'side', 'root', 1)")
        conn.execute("INSERT INTO branches VALUES ('main', 'chat', 'main', 'summary', NULL, NULL, 'ACTIVE', CURRENT_TIMESTAMP)")
        conn.execute("INSERT INTO branches VALUES ('side', 'chat', 'side', 'source', 'source', 'root', 'MERGED', CURRENT_TIMESTAMP)")
        messages_before = conn.execute("SELECT * FROM messages ORDER BY id").fetchall()
        branches_before = conn.execute("SELECT * FROM branches ORDER BY id").fetchall()
        conn.commit()
    for arguments in (("upgrade", "head"), ("upgrade", "head"), ("check",)):
        result = run_database_command(database, "alembic", *arguments)
        assert result.returncode == 0, result.stderr
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        assert conn.execute("SELECT * FROM messages ORDER BY id").fetchall() == messages_before
        assert conn.execute("SELECT * FROM branches ORDER BY id").fetchall() == branches_before
        assert conn.execute("SELECT source_head_message_id FROM merge_events").fetchall() == [(None,)]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT count(*) FROM messages_fts WHERE messages_fts MATCH 'Oldsummary'").fetchone() == (1,)
        conn.execute("UPDATE messages SET content='Newsummary' WHERE id='summary'")
        assert conn.execute("SELECT count(*) FROM messages_fts WHERE messages_fts MATCH 'Newsummary'").fetchone() == (1,)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE merge_events SET source_head_message_id='missing'")


def test_provenance_migration_rolls_back_partial_table_rebuild(tmp_path: Path) -> None:
    """A failed copy leaves the original table, revision and rows intact."""
    from alembic import command
    from sqlalchemy import create_engine, event
    from src.storage.migrations import migration_config

    engine = create_engine(f"sqlite:///{(tmp_path / 'rollback.db').as_posix()}")
    try:
        with engine.connect() as connection:
            command.upgrade(migration_config(connection), "0001")
            connection.exec_driver_sql("INSERT INTO chats VALUES ('chat', 'Old', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
            connection.commit()

            def fail_copy(conn, cursor, statement, parameters, context, executemany) -> None:
                if statement.lstrip().startswith("INSERT INTO _alembic_tmp_merge_events"):
                    raise RuntimeError("injected provenance migration failure")

            event.listen(connection, "before_cursor_execute", fail_copy)
            with pytest.raises(RuntimeError, match="injected provenance"):
                command.upgrade(migration_config(connection), "head")
            event.remove(connection, "before_cursor_execute", fail_copy)
            assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "0001"
            assert "source_head_message_id" not in {
                row[1] for row in connection.exec_driver_sql("PRAGMA table_info(merge_events)")
            }
            assert connection.exec_driver_sql("SELECT id FROM chats").scalar_one() == "chat"
    finally:
        engine.dispose()
