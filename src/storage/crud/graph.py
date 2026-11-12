import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.storage.models import (
    Branch,
    BranchStatus,
    MergeEvent,
    Message,
    MessageRole,
)


class GraphEngineError(Exception):
    """Базовое исключение для ошибок графового ядра."""


class BranchNotFoundError(GraphEngineError):
    """Исключение: указанная ветка не найдена."""


class BranchAlreadyExistsError(GraphEngineError):
    """Исключение: ветка с таким именем уже существует в чате."""


class MessageNotFoundError(GraphEngineError):
    """Исключение: целевое сообщение не найдено."""


class NoActiveMessagesToMergeError(GraphEngineError):
    """Исключение: отсутствуют активные сообщения для слияния ветки."""


async def reconstruct_context(
    chat_id: str,
    branch_name: str,
    session: AsyncSession,
) -> list[Message]:
    """Восстанавливает линейную хронологию активных реплик с санитизацией ролей под API."""
    stmt = select(Branch).where(
        Branch.chat_id == chat_id,
        Branch.name == branch_name,
    )
    branch = (await session.execute(stmt)).scalar_one_or_none()
    if not branch:
        raise BranchNotFoundError(
            f"Ветка '{branch_name}' не найдена в чате {chat_id}"
        )

    curr_id: Optional[str] = branch.head_message_id
    active_messages: list[Message] = []

    while curr_id is not None:
        msg = await session.get(Message, curr_id)
        if not msg:
            break
        if msg.is_active:
            active_messages.append(msg)
        curr_id = msg.parent_id

    timeline = list(reversed(active_messages))

    sanitized_timeline: list[Message] = []
    for msg in timeline:
        if (
            sanitized_timeline
            and sanitized_timeline[-1].role == MessageRole.MODEL
            and msg.role == MessageRole.MODEL
        ):
            sanitized_timeline[-1].content += f"\n\n{msg.content}"
        else:
            sanitized_timeline.append(msg)

    return sanitized_timeline


async def branch_from_here(
    chat_id: str,
    target_message_id: str,
    new_branch_name: str,
    session: AsyncSession,
) -> Branch:
    """Создает новую ветку с точкой отпочкования от указанного сообщения."""
    target_msg = await session.get(Message, target_message_id)
    if not target_msg or target_msg.chat_id != chat_id:
        raise MessageNotFoundError(
            f"Узел {target_message_id} не найден в чате {chat_id}"
        )

    stmt_check = select(Branch).where(
        Branch.chat_id == chat_id,
        Branch.name == new_branch_name,
    )
    if (await session.execute(stmt_check)).scalar_one_or_none():
        raise BranchAlreadyExistsError(
            f"Ветка '{new_branch_name}' уже существует в чате {chat_id}"
        )

    new_branch = Branch(
        id=str(uuid.uuid4()),
        chat_id=chat_id,
        name=new_branch_name,
        head_message_id=target_message_id,
        forked_from_id=target_message_id,
        status=BranchStatus.ACTIVE,
    )
    session.add(new_branch)
    await session.commit()
    await session.refresh(new_branch)
    return new_branch


async def update_message_content(
    message_id: str,
    new_content: str,
    force: bool = False,
    session: Optional[AsyncSession] = None,
) -> dict[str, Any]:
    """Модифицирует текст сообщения с защитой от изменения общего предка веток."""
    if session is None:
        raise ValueError("Требуется активная асинхронная сессия БД.")

    msg = await session.get(Message, message_id)
    if not msg:
        raise MessageNotFoundError(f"Сообщение {message_id} не найдено")

    stmt_children = select(func.count(Message.id)).where(
        Message.parent_id == message_id
    )
    children_count = (await session.execute(stmt_children)).scalar() or 0

    if children_count > 1 and not force:
        return {
            "status": "warning_required",
            "children_count": children_count,
            "message": (
                f"Узел является общим предком для {children_count} ветвлений. "
                "Передайте force=True для подтверждения мутации."
            ),
        }

    msg.content = new_content
    msg.is_edited = True
    msg.updated_at = datetime.now(timezone.utc)

    await session.commit()
    return {"status": "success", "message_id": message_id}


async def toggle_message_active(
    message_id: str,
    session: AsyncSession,
) -> bool:
    """Инвертирует флаг присутствия реплики в активном контексте генерации."""
    msg = await session.get(Message, message_id)
    if not msg:
        raise MessageNotFoundError(f"Сообщение {message_id} не найдено")

    msg.is_active = not msg.is_active
    await session.commit()
    return msg.is_active


async def squash_merge_branch(
    chat_id: str,
    source_branch_name: str,
    target_branch_name: str = "main",
    summarizer_func: Optional[Callable[..., Any]] = None,
    session: Optional[AsyncSession] = None,
) -> dict[str, Any]:
    """Выполняет инкрементальное сквош-слияние дельты побочной ветки в целевую."""
    if session is None or summarizer_func is None:
        raise ValueError("Требуются сессия БД и функция суммаризации.")

    stmt_source = select(Branch).where(
        Branch.chat_id == chat_id,
        Branch.name == source_branch_name,
    )
    source_branch = (await session.execute(stmt_source)).scalar_one_or_none()

    stmt_target = select(Branch).where(
        Branch.chat_id == chat_id,
        Branch.name == target_branch_name,
    )
    target_branch = (await session.execute(stmt_target)).scalar_one_or_none()

    if not source_branch or not target_branch:
        raise BranchNotFoundError("Исходная или целевая ветка не найдена.")

    start_node_id = (
        source_branch.last_merged_message_id
        if source_branch.last_merged_message_id is not None
        else source_branch.forked_from_id
    )

    delta_messages: list[Message] = []
    curr_id: Optional[str] = source_branch.head_message_id

    while curr_id is not None and curr_id != start_node_id:
        msg = await session.get(Message, curr_id)
        if not msg:
            break
        delta_messages.append(msg)
        curr_id = msg.parent_id

    active_delta = [m for m in delta_messages if m.is_active]
    if not active_delta:
        raise NoActiveMessagesToMergeError(
            "Отсутствуют активные сообщения для слияния."
        )

    active_delta_ordered = list(reversed(active_delta))
    summary_text = await summarizer_func(active_delta_ordered)

    merge_msg = MergeEvent(
        id=str(uuid.uuid4()),
        chat_id=chat_id,
        parent_id=target_branch.head_message_id,
        role=MessageRole.SYSTEM_MERGE,
        content=summary_text,
        is_active=True,
        source_branch_name=source_branch_name,
        forked_from_msg_id=start_node_id if start_node_id else target_branch.head_message_id,
        compressed_messages_count=len(active_delta),
    )
    session.add(merge_msg)
    await session.flush()

    target_branch.head_message_id = merge_msg.id
    source_branch.last_merged_message_id = source_branch.head_message_id

    await session.commit()

    return {
        "status": "success",
        "merge_message_id": merge_msg.id,
        "source_branch": source_branch_name,
        "target_branch": target_branch_name,
        "compressed_messages_count": len(active_delta),
        "summary": summary_text,
    }