"""Read bounded payloads from a validated branch path within a caller-owned snapshot."""

from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.storage.crud.graph import BranchNotFoundError, GraphIntegrityError, MessageNotFoundError
from src.storage.models import Branch, Message, MessageRole


@dataclass(frozen=True)
class TimelineSlice:
    branch: Branch
    messages: list[Mapping[str, Any]]
    has_older: bool
    has_newer: bool
    older_cursor: str | None
    newer_cursor: str | None
    last_active_dialogue: tuple[str, MessageRole] | None


async def read_timeline_window(
    session: AsyncSession, branch_id: str, *, mode: str, anchor_id: str | None,
    radius: int = 20, limit: int = 40,
) -> TimelineSlice:
    """Validate compact ancestry first and select full text only for the requested IDs."""
    branch = await session.get(Branch, branch_id)
    if branch is None:
        raise BranchNotFoundError(f"Ветка {branch_id} не найдена.")
    table = Message.__table__
    rows = (await session.execute(select(
        table.c.id, table.c.parent_id, table.c.role, table.c.is_active,
    ).where(table.c.chat_id == branch.chat_id))).all()
    by_id = {row.id: row for row in rows}
    path: list[str] = []
    visited: set[str] = set()
    latest: tuple[str, MessageRole] | None = None
    cursor = branch.head_message_id
    while cursor is not None:
        row = by_id.get(cursor)
        if row is None or cursor in visited:
            raise GraphIntegrityError("Invalid branch ancestry.")
        visited.add(cursor)
        path.append(cursor)
        if latest is None and row.is_active and row.role in (MessageRole.USER, MessageRole.MODEL):
            latest = (cursor, row.role)
        cursor = row.parent_id
    path.reverse()
    if mode == "tail":
        start, end = max(0, len(path) - limit), len(path)
    else:
        if anchor_id not in visited:
            raise MessageNotFoundError("Сообщение не найдено в истории выбранной ветки.")
        position = path.index(anchor_id)
        if mode == "around":
            start, end = max(0, position - radius), min(len(path), position + radius + 1)
        elif mode == "older":
            start, end = max(0, position - limit), position
        else:
            start, end = position + 1, min(len(path), position + limit + 1)
    identities = path[start:end]
    messages: list[Mapping[str, Any]] = []
    if identities:
        payloads = (await session.execute(select(table).where(
            table.c.chat_id == branch.chat_id, table.c.id.in_(identities),
        ))).mappings().all()
        payload_by_id = {row["id"]: row for row in payloads}
        messages = [payload_by_id[identity] for identity in identities]
    older = bool(identities) and start > 0
    newer = bool(identities) and end < len(path)
    return TimelineSlice(branch, messages, older, newer,
                         identities[0] if older else None, identities[-1] if newer else None, latest)
