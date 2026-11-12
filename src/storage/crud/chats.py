import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.storage.crud.graph import BranchNotFoundError
from src.storage.models import (
    Branch,
    Chat,
    MergeEvent,
    Message,
    MessageRole,
    ModelMessage,
)


async def get_branch(
    chat_id: str,
    branch_name: str,
    session: AsyncSession,
) -> Optional[Branch]:
    """Возвращает объект ветки по идентификатору чата и символьному имени."""
    stmt = select(Branch).where(
        Branch.chat_id == chat_id,
        Branch.name == branch_name,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def append_message(
    chat_id: str,
    branch_name: str,
    role: MessageRole,
    content: str,
    session: AsyncSession,
    metadata_json: Optional[str] = None,
) -> Message:
    """Добавляет реплику в конец ветки, сдвигает указатель HEAD и обновляет активность чата."""
    branch = await get_branch(chat_id=chat_id, branch_name=branch_name, session=session)
    if not branch:
        raise BranchNotFoundError(
            f"Ветка '{branch_name}' не найдена в чате {chat_id}"
        )

    msg_cls = ModelMessage if role == MessageRole.MODEL else Message
    now = datetime.now(timezone.utc)

    new_msg = msg_cls(
        id=str(uuid.uuid4()),
        chat_id=chat_id,
        parent_id=branch.head_message_id,
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

    await session.commit()
    await session.refresh(new_msg)
    return new_msg


async def get_latest_merge_event(
    chat_id: str,
    branch_name: str,
    session: AsyncSession,
) -> Optional[MergeEvent]:
    """Возвращает последний хронологический узел слияния для указанной ветки."""
    stmt = (
        select(MergeEvent)
        .where(
            MergeEvent.chat_id == chat_id,
            MergeEvent.source_branch_name == branch_name,
        )
        .order_by(MergeEvent.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()