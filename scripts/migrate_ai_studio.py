"""Import a Google AI Studio Prompt Data export into a new ExoCortex chat."""

import argparse
import asyncio
import json
import shutil
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from alembic.util import CommandError
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from src.config import BASE_DIR, settings

DRIVE_KEYS = ("driveImage", "driveDocument", "driveVideo", "driveAudio", "driveFile")
MEDIA_KEYS = (*DRIVE_KEYS, "inlineData", "fileData")


@dataclass(frozen=True)
class Reply:
    role: str
    content: str
    created_at: datetime | None


@dataclass
class History:
    total_chunks: int
    thoughts: int = 0
    empty: int = 0
    replies: list[Reply] = field(default_factory=list)


def latest_export(directory: Path) -> Path:
    """Select the newest JSON export by modification time."""
    candidates = (path for path in directory.glob("*.json") if path.is_file())
    newest = max(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name), default=None)
    if newest is None:
        raise ValueError(f"В папке {directory} нет JSON-файлов. Укажите путь через --file.")
    return newest


def chunk_text(chunk: dict[str, object], parts: list[dict[str, object]]) -> str:
    """Preserve text verbatim, or describe media without copying its payload."""
    if "text" in chunk:
        text = chunk["text"]
        if not isinstance(text, str):
            raise ValueError("поле text должно быть строкой")
        return text

    pieces: list[str] = []
    for part in (chunk, *parts):
        for key in MEDIA_KEYS:
            if key not in part:
                continue
            media = part[key]
            if isinstance(media, dict):
                drive_id = media.get("id") or media.get("fileId")
                if key in DRIVE_KEYS and isinstance(drive_id, str) and drive_id:
                    pieces.append(f"[Вложение Google Drive: id={drive_id}]")
                    continue
                mime = media.get("mimeType")
                if isinstance(mime, str) and mime:
                    pieces.append(f"[Медиа-вложение: type={mime}]")
                    continue
            pieces.append(f"[Медиа-вложение: type={key}]")
    return "\n".join(pieces)


def read_export(path: Path) -> History:
    """Validate dialogue chunks while retaining their order in the export."""
    with path.open(encoding="utf-8-sig") as source:
        root = json.load(source)
    prompt = root.get("chunkedPrompt") if isinstance(root, dict) else None
    chunks = prompt.get("chunks") if isinstance(prompt, dict) else None
    if not isinstance(chunks, list):
        raise ValueError("Экспорт должен содержать массив chunkedPrompt.chunks.")

    history = History(total_chunks=len(chunks))
    for index, chunk in enumerate(chunks):
        try:
            if not isinstance(chunk, dict):
                raise ValueError("ожидался объект")
            parts = chunk.get("parts", [])
            if chunk.get("isThought") is True or (
                isinstance(parts, list)
                and any(isinstance(part, dict) and part.get("thought") is True for part in parts)
            ):
                history.thoughts += 1
                continue
            if not isinstance(parts, list) or any(not isinstance(part, dict) for part in parts):
                raise ValueError("поле parts должно быть массивом объектов")
            content = chunk_text(chunk, parts)
            if not content.strip():
                history.empty += 1
                continue
            role = chunk.get("role")
            if not isinstance(role, str) or role not in ("user", "model"):
                raise ValueError("поле role должно быть user или model")
            raw_time = chunk.get("createTime")
            created_at = None
            if "createTime" in chunk:
                if not isinstance(raw_time, str):
                    raise ValueError("поле createTime должно быть строкой ISO-8601")
                created_at = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                if created_at.tzinfo is None:
                    raise ValueError("поле createTime должно содержать часовой пояс")
                created_at = created_at.astimezone(timezone.utc)
            history.replies.append(Reply(role, content, created_at))
        except ValueError as error:
            raise ValueError(f"chunkedPrompt.chunks[{index}] — {error}") from error
    if not history.replies:
        raise ValueError("После фильтрации в экспорте не осталось реплик.")
    return history


def resolve_times(replies: list[Reply]) -> list[datetime]:
    """Date undated chunks using the previous timestamp or the first known one."""
    current = next((reply.created_at for reply in replies if reply.created_at is not None), None)
    if current is None:
        raise ValueError("В репликах нет ни одной временной метки createTime.")
    times = []
    for reply in replies:
        if reply.created_at is not None:
            current = reply.created_at
        times.append(current)
    return times


def backup_database(database: Path) -> Path | None:
    """Copy the main SQLite file after checkpointing WAL and excluding writers."""
    if not database.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = database.with_name(f"{database.name}.bak_{stamp}")
    with closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=rw", uri=True)) as conn:
        busy, _, _ = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if busy:
            raise ValueError("Не удалось сохранить WAL в SQLite. Завершите активные операции и повторите импорт.")
        conn.execute("BEGIN IMMEDIATE")
        # A writer may have committed between the checkpoint and the lock.
        wal = database.with_name(f"{database.name}-wal")
        if wal.exists() and wal.stat().st_size:
            raise ValueError("SQLite изменилась при создании бэкапа. Повторите импорт.")
        with backup.open("xb"):
            pass
        try:
            shutil.copy2(database, backup)
        except OSError:
            backup.unlink(missing_ok=True)
            raise
    return backup


async def save_history(title: str, replies: list[Reply], times: list[datetime]) -> tuple[str, str]:
    """Commit the chat, ordered message chain and main branch in one transaction."""
    from src.storage.database import async_session_factory, engine
    from src.storage.migrations import upgrade_schema
    from src.storage.models import Branch, BranchStatus, Chat, Message, MessageRole, ModelMessage

    chat_id = str(uuid4())
    parent_id = None
    try:
        await upgrade_schema(engine)
        async with async_session_factory.begin() as session:
            session.add(Chat(id=chat_id, title=title, created_at=times[0], updated_at=times[-1]))
            await session.flush()

            for reply, created_at in zip(replies, times, strict=True):
                role = MessageRole(reply.role)
                message_class = Message if role is MessageRole.USER else ModelMessage
                message_id = str(uuid4())
                session.add(message_class(
                    id=message_id,
                    chat_id=chat_id,
                    parent_id=parent_id,
                    role=role,
                    content=reply.content,
                    is_active=True,
                    created_at=created_at,
                ))
                parent_id = message_id
            await session.flush()
            session.add(Branch(
                id=str(uuid4()),
                chat_id=chat_id,
                name="main",
                head_message_id=parent_id,
                forked_from_id=None,
                status=BranchStatus.ACTIVE,
                created_at=times[0],
            ))
    finally:
        await engine.dispose()
    assert parent_id is not None
    return chat_id, parent_id


def main(argv: Sequence[str] | None = None) -> None:
    """Run the CLI from the repository root with python -m scripts.migrate_ai_studio."""
    parser = argparse.ArgumentParser(description="Импорт диалога из Google AI Studio в ExoCortex.")
    parser.add_argument("--file", type=Path, help="Входной JSON. По умолчанию — самый свежий в data/raw.")
    parser.add_argument("--title", help="Название чата. По умолчанию — имя файла без расширения.")
    parser.add_argument("--dry-run", action="store_true", help="Проверить экспорт и показать статистику.")
    args = parser.parse_args(argv)

    try:
        path = args.file if args.file is not None else latest_export(BASE_DIR / "data" / "raw")
        title = args.title if args.title is not None else path.stem
        if not title.strip() or len(title) > 255:
            raise ValueError("Название чата должно быть непустым и содержать не более 255 символов.")
        history = read_export(path)
        replies = history.replies
        times = resolve_times(replies)
        characters = sum(len(reply.content) for reply in replies)
        utf8_bytes = sum(len(reply.content.encode("utf-8")) for reply in replies)
        ids = None
        if not args.dry_run:
            database_url = make_url(settings.DATABASE_URL)
            database_name = database_url.database
            if (
                database_url.drivername != "sqlite+aiosqlite"
                or not database_name
                or database_name == ":memory:"
                or "uri" in database_url.query
            ):
                raise ValueError("DATABASE_URL должен указывать на обычный файл SQLite (sqlite+aiosqlite).")
            backup = backup_database(Path(database_name).resolve())
            if backup is not None:
                print(f"Резервная копия: {backup.resolve()}", flush=True)
            ids = asyncio.run(save_history(title, replies, times))
    except SQLAlchemyError as error:
        parser.exit(1, f"Ошибка импорта в SQLite ({type(error).__name__}).\n")
    except (OSError, ValueError, OverflowError, sqlite3.Error, CommandError) as error:
        parser.exit(1, f"Ошибка импорта: {error}\n")

    users = sum(reply.role == "user" for reply in replies)
    print("[DRY RUN COMPLETED]" if ids is None else "[MIGRATION SUCCESSFUL]")
    print(f"Файл: {path}")
    print(f"Чат: {title}")
    print(f"Всего чанков: {history.total_chunks}")
    print(f"Отфильтровано размышлений: {history.thoughts}")
    print(f"Пропущено пустых чанков: {history.empty}")
    print(f"Реплик пользователя (USER): {users}")
    print(f"Ответов модели (MODEL): {len(replies) - users}")
    print(f"Реплик с восстановленной датой: {sum(reply.created_at is None for reply in replies)}")
    print(f"Временной диапазон (UTC): {min(times).isoformat()} — {max(times).isoformat()}")
    print(f"Объём текста: {characters} символов / {utf8_bytes / 1024:.2f} КиБ (UTF-8)")
    print(f"Chat ID: {ids[0] if ids else 'не создан'}")
    print(f"HEAD Message ID: {ids[1] if ids else 'не создан'}")


if __name__ == "__main__":
    main()
