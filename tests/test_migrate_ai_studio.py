import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from scripts import migrate_ai_studio


def write_export(path: Path, chunks: list[dict[str, object]]) -> None:
    path.write_text(json.dumps({"chunkedPrompt": {"chunks": chunks}}), encoding="utf-8")


def run_import(database: Path, export: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "scripts.migrate_ai_studio", "--file", str(export), *args],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{database.as_posix()}", "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


def test_dry_run_validates_export_without_touching_database(tmp_path: Path) -> None:
    database = tmp_path / "missing" / "cortex.db"
    export = tmp_path / "История.json"
    write_export(export, [
        {"role": "user", "text": "Вопрос", "createTime": "2026-05-16T14:44:00.836Z"},
        {"role": "model", "text": "Reasoning", "isThought": True},
        {"role": "model", "text": "Also reasoning", "parts": [{"thought": True}]},
        {"text": " \n "},
        {"role": "model", "text": "Ответ", "createTime": "2026-05-16T14:45:00Z"},
    ])

    result = run_import(database, export, "--dry-run")

    assert result.returncode == 0, result.stderr
    assert "[DRY RUN COMPLETED]" in result.stdout
    assert "Чат: История" in result.stdout
    assert "Всего чанков: 5" in result.stdout
    assert "Отфильтровано размышлений: 2" in result.stdout
    assert "Пропущено пустых чанков: 1" in result.stdout
    assert "Реплик пользователя (USER): 1" in result.stdout
    assert "Ответов модели (MODEL): 1" in result.stdout
    assert not database.parent.exists()

    write_export(export, [{}, {"text": "valid JSON, invalid chunk"}])
    invalid = run_import(database, export, "--dry-run")
    assert invalid.returncode == 1
    assert "chunkedPrompt.chunks[1]" in invalid.stderr
    assert not database.parent.exists()


def test_automatic_selection_uses_newest_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(migrate_ai_studio, "BASE_DIR", tmp_path)
    raw = tmp_path / "data" / "raw"
    raw.mkdir(parents=True)
    with pytest.raises(SystemExit) as error:
        migrate_ai_studio.main(["--dry-run"])
    assert error.value.code == 1
    assert "нет JSON-файлов" in capsys.readouterr().err

    old = raw / "a-old.json"
    old.touch()
    os.utime(old, (1_000, 1_000))
    newest = raw / "b-newest.json"
    write_export(newest, [{"role": "user", "text": "Question", "createTime": "2026-05-16T14:44:00Z"}])
    (raw / "c-ignore.txt").touch()

    migrate_ai_studio.main(["--dry-run"])
    assert f"Файл: {newest}" in capsys.readouterr().out


def test_import_copies_committed_wal_and_preserves_long_chain(tmp_path: Path) -> None:
    """Verify that the standalone backup includes WAL data and predates the import."""
    database = tmp_path / "cortex.db"
    export = tmp_path / "history.json"
    chunks: list[dict[str, object]] = [
        {"role": "user", "driveDocument": {"id": "file-123"}},
        {"role": "model", "text": "  first answer\n", "createTime": "2026-05-16T17:44:00.836+03:00"},
        {"role": "user", "parts": [{"inlineData": {"mimeType": "image/png", "data": "omitted"}}]},
    ]
    chunks.extend(
        {"role": "user" if index % 2 else "model", "text": "searchable " * 200}
        for index in range(2_000)
    )
    chunks.append({"role": "model", "text": "final answer", "createTime": "2026-05-16T14:50:00Z"})
    write_export(export, chunks)

    with closing(sqlite3.connect(database)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE marker (content TEXT)")
        conn.execute("INSERT INTO marker VALUES ('committed before import')")
        conn.commit()
        assert (tmp_path / "cortex.db-wal").stat().st_size > 0

        result = run_import(database, export, "--title", "Imported")

        assert result.returncode == 0, result.stderr
        assert "[MIGRATION SUCCESSFUL]" in result.stdout
        (backup,) = tmp_path.glob("cortex.db.bak_*")
        assert str(backup) in result.stdout
        with closing(sqlite3.connect(backup)) as snapshot:
            assert snapshot.execute("SELECT content FROM marker").fetchone() == ("committed before import",)
            assert snapshot.execute("SELECT name FROM sqlite_master WHERE name = 'chats'").fetchone() is None

        chat_id, title, start, end = conn.execute(
            "SELECT id, title, created_at, updated_at FROM chats",
        ).fetchone()
        assert (title, start, end) == ("Imported", "2026-05-16 14:44:00.836000", "2026-05-16 14:50:00.000000")
        name, status, head, fork = conn.execute(
            "SELECT name, status, head_message_id, forked_from_id FROM branches WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        assert (name, status, fork) == ("main", "ACTIVE", None)
        messages = conn.execute(
            "SELECT id, parent_id, role, content, is_active FROM messages WHERE chat_id = ? ORDER BY rowid",
            (chat_id,),
        ).fetchall()
        assert len(messages) == len(chunks)
        assert messages[0][1:] == (None, "USER", "[Вложение Google Drive: id=file-123]", 1)
        assert messages[1][2:] == ("MODEL", "  first answer\n", 1)
        assert messages[2][3] == "[Медиа-вложение: type=image/png]"
        assert all(current[1] == previous[0] for previous, current in zip(messages, messages[1:]))
        assert all(message[4] == 1 for message in messages)
        assert messages[-1][0] == head
        assert f"Chat ID: {chat_id}" in result.stdout
        assert f"HEAD Message ID: {head}" in result.stdout
        assert conn.execute("SELECT count(*) FROM messages_fts WHERE messages_fts MATCH 'searchable'").fetchone() == (2_000,)


def test_failed_branch_insert_rolls_back_chat_and_messages(tmp_path: Path) -> None:
    database = tmp_path / "cortex.db"
    export = tmp_path / "history.json"
    write_export(export, [{"role": "user", "text": "Question", "createTime": "2026-05-16T14:44:00Z"}])
    first = run_import(database, export)
    assert first.returncode == 0, first.stderr

    with closing(sqlite3.connect(database)) as conn:
        conn.execute("CREATE TRIGGER reject_import BEFORE INSERT ON branches BEGIN SELECT RAISE(ABORT, 'test'); END")
        conn.commit()
        failed = run_import(database, export)
        assert failed.returncode == 1
        assert "Ошибка импорта в SQLite" in failed.stderr
        for table in ("chats", "messages", "branches"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone() == (1,)
        (backup,) = tmp_path.glob("cortex.db.bak_*")
        assert str(backup) in failed.stdout
