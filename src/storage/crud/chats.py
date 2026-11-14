import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.storage.crud.graph import (
    BranchConflictError,
    BranchNotFoundError,
    GraphIntegrityError,
    get_branch as get_branch,
    get_latest_merge_event as get_latest_merge_event,
    message_belongs_to_chat,
    reserve_branch_write,
)
from src.storage.models import (
    Chat,
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
