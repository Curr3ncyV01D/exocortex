"""Read and validate a chat graph without loading full ORM message objects."""

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.storage.crud.graph import GraphIntegrityError
from src.storage.models import Branch, Chat, MergeEvent, Message, MessageRole


@dataclass(frozen=True)
class GraphSnapshot:
    messages: list[Mapping[str, Any]]
    branches: list[Branch]
    merges: list[Mapping[str, Any]]


def _validate_graph(snapshot: GraphSnapshot) -> None:
    """Validate all parent chains in linear time, including unassigned nodes."""
    nodes = {node["id"]: node for node in snapshot.messages}

    def require_node(identity: str | None) -> None:
        if identity is not None and identity not in nodes:
            raise GraphIntegrityError("Graph reference points outside the chat.")

    completed: set[str] = set()
    for identity in nodes:
        path: set[str] = set()
        current: str | None = identity
        while current is not None and current not in completed:
            require_node(current)
            if current in path:
                raise GraphIntegrityError("Message parent chain contains a cycle.")
            path.add(current)
            current = nodes[current]["parent_id"]
        completed.update(path)
    for branch in snapshot.branches:
        for identity in (branch.head_message_id, branch.forked_from_id, branch.last_merged_message_id):
            require_node(identity)
    merge_ids = {merge["message_id"] for merge in snapshot.merges}
    expected_ids = {node["id"] for node in snapshot.messages if node["role"] == MessageRole.SYSTEM_MERGE}
    if merge_ids != expected_ids:
        raise GraphIntegrityError("Merge records do not match system merge messages.")
    for merge in snapshot.merges:
        require_node(merge["source_head_message_id"])
        require_node(merge["forked_from_msg_id"])


async def read_chat_graph(session: AsyncSession, chat_id: str) -> GraphSnapshot | None:
    """Read graph data inside a caller-owned SQLite snapshot transaction."""
    if await session.scalar(select(Chat.id).where(Chat.id == chat_id)) is None:
        return None
    messages = Message.__table__
    columns = [messages.c[name] for name in (
        "id", "parent_id", "role", "content", "is_active", "is_edited", "created_at", "updated_at",
    )]
    nodes = (await session.execute(
        select(*columns).where(messages.c.chat_id == chat_id).order_by(messages.c.created_at, messages.c.id)
    )).mappings().all()
    branches = list(await session.scalars(
        select(Branch).where(Branch.chat_id == chat_id).order_by(Branch.created_at, Branch.id)
    ))
    merges = MergeEvent.__table__
    merge_rows = (await session.execute(
        select(merges).where(merges.c.chat_id == chat_id).order_by(merges.c.message_id)
    )).mappings().all()
    snapshot = GraphSnapshot(list(nodes), branches, list(merge_rows))
    _validate_graph(snapshot)
    return snapshot
