import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.storage.crud.graph import (
    LATEST_MERGE_EVENT_ORDER,
    BranchConflictError,
    BranchNotFoundError,
    GraphIntegrityError,
    get_branch as get_branch,
    get_latest_merge_event as get_latest_merge_event,
    message_belongs_to_chat,
    reserve_branch_write,
)
from src.storage.models import (
    Branch,
    BranchStatus,
    Chat,
    MergeEvent,
    Message,
    MessageRole,
    ModelMessage,
)


class _ExpectedHead(Enum):
    UNSPECIFIED = 0



async def append_message(
    chat_id: str,
    branch_name: str,
    role: MessageRole,
    content: str,
    session: AsyncSession,
    metadata_json: Optional[str] = None,
    *,
    expected_head_id: str | None | _ExpectedHead = _ExpectedHead.UNSPECIFIED,
) -> Message:
    """Добавляет реплику и обновляет ветку. Транзакцию завершает вызывающий код."""
    if role not in (MessageRole.USER, MessageRole.MODEL):
        raise ValueError("Обычная реплика должна иметь роль USER или MODEL.")
    branch = await get_branch(chat_id=chat_id, branch_name=branch_name, session=session)
    if not branch:
        raise BranchNotFoundError(
            f"Ветка '{branch_name}' не найдена в чате {chat_id}"
        )

    head_id = branch.head_message_id
    if expected_head_id is not _ExpectedHead.UNSPECIFIED and head_id != expected_head_id:
        raise BranchConflictError("Состояние ветки изменилось. Повторите операцию.")
    if head_id is not None and not await message_belongs_to_chat(chat_id, head_id, session):
        raise GraphIntegrityError(f"Некорректная вершина ветки в чате {chat_id}.")
    await reserve_branch_write(session, branch.id, head_id, branch.last_merged_message_id)

    msg_cls = ModelMessage if role == MessageRole.MODEL else Message
    now = datetime.now(timezone.utc)

    new_msg = msg_cls(
        id=str(uuid.uuid4()),
        chat_id=chat_id,
        parent_id=head_id,
        role=role,
        content=content,
        is_active=True,
        metadata_json=metadata_json,
        created_at=now,
    )
    session.add(new_msg)
    await session.flush()

    branch.head_message_id = new_msg.id
    await session.execute(
        update(Chat).where(Chat.id == chat_id).values(updated_at=now)
    )

    await session.flush()
    return new_msg


@dataclass(frozen=True)
class HistoryNeighborhood:
    messages: list[Message]
    stopped_at_fork: bool


@dataclass(frozen=True)
class BranchMemory:
    name: str
    status: BranchStatus
    latest_summary_active: bool | None


@dataclass(frozen=True)
class ChatMemoryOverview:
    active_count: int
    earliest: datetime | None
    latest: datetime | None
    branches: list[BranchMemory]


async def _preferred_children(
    chat_id: str, head_id: str | None, session: AsyncSession,
) -> dict[str, tuple[str, bool]]:
    """Map parents to their next node on the current branch without reading message text."""
    if head_id is None:
        return {}
    nodes = Message.__table__
    metadata = select(nodes.c.id, nodes.c.parent_id, nodes.c.is_active)
    path = metadata.where(nodes.c.chat_id == chat_id, nodes.c.id == head_id).cte(recursive=True)
    path = path.union(
        metadata.join(path, nodes.c.id == path.c.parent_id).where(nodes.c.chat_id == chat_id)
    )
    rows = await session.execute(select(path.c.id, path.c.parent_id, path.c.is_active))
    return {parent_id: (node_id, active) for node_id, parent_id, active in rows if parent_id is not None}


async def read_context_around(
    chat_id: str, message_id: str, radius: int, session: AsyncSession, *, head_id: str | None = None,
) -> HistoryNeighborhood | None:
    """Return active neighbors on one path, stopping at an unresolved fork."""
    radius = min(max(radius, 1), 5)
    node_query = select(Message.id, Message.parent_id, Message.is_active).where(Message.chat_id == chat_id)
    anchor = (await session.execute(
        node_query.where(Message.id == message_id, Message.is_active.is_(True))
    )).one_or_none()
    if anchor is None:
        return None

    visited = {message_id}
    before: list[str] = []
    parent_id: str | None = anchor.parent_id
    while parent_id is not None and len(before) < radius:
        parent = (await session.execute(node_query.where(Message.id == parent_id))).one_or_none()
        if parent is None or parent_id in visited:
            raise GraphIntegrityError(f"Некорректная связь графа в чате {chat_id}.")
        visited.add(parent_id)
        if parent.is_active:
            before.append(parent_id)
        parent_id = parent.parent_id

    after: list[str] = []
    preferred: dict[str, tuple[str, bool]] | None = None
    cursor = message_id
    stopped_at_fork = False
    while cursor != head_id and len(after) < radius:
        children = (await session.execute(node_query.where(Message.parent_id == cursor).limit(2))).all()
        if not children:
            break
        if len(children) == 1:
            child_id, _, active = children[0]
        else:
            if preferred is None:
                preferred = await _preferred_children(chat_id, head_id, session)
            child = preferred.get(cursor)
            if child is None:
                stopped_at_fork = True
                break
            child_id, active = child
        if child_id in visited:
            raise GraphIntegrityError(f"Некорректная связь графа в чате {chat_id}.")
        visited.add(child_id)
        if active:
            after.append(child_id)
        cursor = child_id

    ordered_ids = [*reversed(before), message_id, *after]
    messages = await session.scalars(select(Message).where(
        Message.chat_id == chat_id, Message.is_active.is_(True), Message.id.in_(ordered_ids),
    ))
    by_id = {message.id: message for message in messages}
    return HistoryNeighborhood([by_id[node_id] for node_id in ordered_ids if node_id in by_id], stopped_at_fork)


async def get_chat_memory_overview(chat_id: str, session: AsyncSession) -> ChatMemoryOverview:
    """Summarize active history and the availability of each branch's latest merge."""
    count, earliest, latest = (await session.execute(
        select(func.count(Message.id), func.min(Message.created_at), func.max(Message.created_at))
        .where(Message.chat_id == chat_id, Message.is_active.is_(True))
    )).one()
    latest_summary_active = (
        select(MergeEvent.is_active)
        .select_from(MergeEvent)
        .where(MergeEvent.chat_id == Branch.chat_id, MergeEvent.source_branch_name == Branch.name)
        .order_by(*LATEST_MERGE_EVENT_ORDER)
        .limit(1)
        .correlate(Branch)
        .scalar_subquery()
    )
    branches = await session.execute(
        select(Branch.name, Branch.status, latest_summary_active)
        .where(Branch.chat_id == chat_id)
        .order_by(Branch.name)
    )
    return ChatMemoryOverview(count, earliest, latest, [
        BranchMemory(name, status, summary_active) for name, status, summary_active in branches
    ])
