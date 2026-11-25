import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Literal, Optional, TypedDict

from sqlalchemy import func, literal_column, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

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


class BranchConflictError(GraphEngineError):
    """Состояние ветки изменилось после чтения."""


class MessageNotFoundError(GraphEngineError):
    """Исключение: целевое сообщение не найдено."""


class GraphIntegrityError(GraphEngineError):
    """Сохранённые связи графа нарушают его инварианты."""


class InvalidMergeError(GraphEngineError, ValueError):
    """Слияние нарушает правила MVP или не получило текст выжимки."""


class NoActiveMessagesToMergeError(GraphEngineError):
    """Исключение: отсутствуют активные сообщения для слияния ветки."""


class EditSuccess(TypedDict):
    status: Literal["success"]
    message_id: str


class EditWarning(TypedDict):
    status: Literal["warning_required"]
    affected_branch_count: int
    message: str


class MergeResult(TypedDict):
    status: Literal["success"]
    merge_message_id: str
    source_branch: str
    target_branch: str
    compressed_messages_count: int
    summary: str


async def get_branch(
    chat_id: str, branch_name: str, session: AsyncSession, *, refresh: bool = False,
) -> Optional[Branch]:
    statement = select(Branch).where(Branch.chat_id == chat_id, Branch.name == branch_name)
    return await session.scalar(
        statement.execution_options(populate_existing=refresh)
    )


LATEST_MERGE_EVENT_ORDER = (MergeEvent.created_at.desc(), literal_column("messages.rowid").desc())


async def get_latest_merge_event(
    chat_id: str, branch_name: str, session: AsyncSession,
) -> Optional[MergeEvent]:
    return await session.scalar(
        select(MergeEvent)
        .where(MergeEvent.chat_id == chat_id, MergeEvent.source_branch_name == branch_name)
        .order_by(*LATEST_MERGE_EVENT_ORDER)
        .execution_options(populate_existing=True)
        .limit(1)
    )


async def reserve_branch_write(
    session: AsyncSession,
    branch_id: str,
    head_id: str | None,
    last_merged_id: Optional[str],
) -> None:
    """Проверяет состояние ветки и удерживает право записи SQLite до конца транзакции."""
    statement = (
        update(Branch)
        .where(
            Branch.id == branch_id,
            Branch.head_message_id == head_id,
            Branch.last_merged_message_id == last_merged_id,
        )
        .values(head_message_id=Branch.head_message_id)
        .returning(Branch.id)
        .execution_options(synchronize_session=False)
    )
    if (await session.execute(statement)).scalar_one_or_none() is None:
        raise BranchConflictError("Состояние ветки изменилось. Повторите операцию.")


async def reconstruct_context(
    chat_id: str,
    branch_name: str,
    session: AsyncSession,
) -> list[Message]:
    """Возвращает активные узлы ветки в порядке истории."""
    branch = await get_branch(chat_id, branch_name, session)
    if not branch:
        raise BranchNotFoundError(
            f"Ветка '{branch_name}' не найдена в чате {chat_id}"
        )

    return await read_context_from_head(chat_id, branch.head_message_id, session)


async def read_context_from_head(
    chat_id: str, head_id: str | None, session: AsyncSession, *, include_inactive: bool = False,
) -> list[Message]:
    """Читает цепочку от заданной вершины в хронологическом порядке."""
    history = await _read_history(chat_id, head_id, session)
    return [msg for msg in reversed(history) if include_inactive or msg.is_active]


def latest_dialogue_message(history: list[Message]) -> Message | None:
    return next(
        (msg for msg in reversed(history) if msg.is_active and msg.role in (MessageRole.USER, MessageRole.MODEL)),
        None,
    )


async def _read_history(
    chat_id: str,
    head_id: str | None,
    session: AsyncSession,
    *,
    stop_id: Optional[str] = None,
) -> list[Message]:
    """Читает цепочку от вершины до границы, исключая саму границу."""
    curr_id: Optional[str] = head_id
    history: list[Message] = []
    visited: set[str] = set()

    while curr_id is not None and curr_id != stop_id:
        msg = await session.get(Message, curr_id)
        if msg is None or msg.chat_id != chat_id or curr_id in visited:
            raise GraphIntegrityError(f"Некорректная связь графа в чате {chat_id}.")
        visited.add(curr_id)
        history.append(msg)
        curr_id = msg.parent_id

    if stop_id is not None and (
        curr_id != stop_id or not await message_belongs_to_chat(chat_id, stop_id, session)
    ):
        raise GraphIntegrityError(f"Некорректная граница слияния в чате {chat_id}.")
    return history


async def message_belongs_to_chat(chat_id: str, message_id: str, session: AsyncSession) -> bool:
    message = await session.get(Message, message_id)
    return message is not None and message.chat_id == chat_id


async def branch_from_here(
    chat_id: str,
    target_message_id: str,
    new_branch_name: str,
    session: AsyncSession,
) -> Branch:
    """Создает ветку. Транзакцию завершает вызывающий код."""
    if not await message_belongs_to_chat(chat_id, target_message_id, session):
        raise MessageNotFoundError(
            f"Узел {target_message_id} не найден в чате {chat_id}"
        )

    statement = (
        insert(Branch)
        .values(
            id=str(uuid.uuid4()), chat_id=chat_id, name=new_branch_name,
            head_message_id=target_message_id, forked_from_id=target_message_id,
            status=BranchStatus.ACTIVE,
        )
        .on_conflict_do_nothing(index_elements=[Branch.chat_id, Branch.name])
        .returning(Branch)
    )
    new_branch = await session.scalar(statement)
    if new_branch is None:
        raise BranchAlreadyExistsError(
            f"Ветка '{new_branch_name}' уже существует в чате {chat_id}"
        )

    return new_branch


async def update_message_content(
    message_id: str,
    new_content: str,
    force: bool = False,
    *,
    session: AsyncSession,
) -> EditSuccess | EditWarning:
    """Модифицирует текст сообщения. Транзакцию завершает вызывающий код."""
    msg = await session.get(Message, message_id)
    if not msg:
        raise MessageNotFoundError(f"Сообщение {message_id} не найдено")
    if msg.content == new_content:
        return {"status": "success", "message_id": message_id}

    if not force:
        messages = Message.__table__
        descendants = (
            select(messages.c.id).where(messages.c.id == message_id)
            .cte("descendants", recursive=True)
        )
        descendants = descendants.union(
            select(messages.c.id)
            .join(descendants, messages.c.parent_id == descendants.c.id)
            .where(messages.c.chat_id == msg.chat_id)
        )
        count = await session.scalar(
            select(func.count()).select_from(Branch)
            .join(descendants, Branch.head_message_id == descendants.c.id)
            .where(Branch.chat_id == msg.chat_id)
        )
        if count is not None and count > 1:
            return {
                "status": "warning_required",
                "affected_branch_count": count,
                "message": f"Правка затронет {count} веток. Передайте force=True для подтверждения.",
            }

    msg.content = new_content
    msg.is_edited = True
    msg.updated_at = datetime.now(timezone.utc)

    await session.flush()
    return {"status": "success", "message_id": message_id}


async def set_message_active(
    message_id: str,
    session: AsyncSession,
    *,
    is_active: bool,
) -> bool:
    """Устанавливает маску идемпотентно. Транзакцию завершает вызывающий код."""
    messages = Message.__table__
    new_state = await session.scalar(
        update(messages).where(messages.c.id == message_id)
        .values(is_active=is_active).returning(messages.c.is_active)
    )
    if new_state is None:
        raise MessageNotFoundError(f"Сообщение {message_id} не найдено")

    if (msg := await session.get(Message, message_id)) is not None:
        set_committed_value(msg, "is_active", new_state)
    return new_state


async def squash_merge_branch(
    chat_id: str,
    source_branch_name: str,
    target_branch_name: str = "main",
    *,
    summarizer_func: Callable[[list[Message], str | None], Awaitable[str]],
    session: AsyncSession,
) -> MergeResult:
    """Сливает активную дельту. Транзакцию завершает вызывающий код."""
    if source_branch_name == target_branch_name:
        raise InvalidMergeError("Нельзя слить ветку в саму себя.")
    if target_branch_name != "main":
        raise InvalidMergeError("Слияние разрешено только в main.")

    source_branch = await get_branch(chat_id, source_branch_name, session)
    target_branch = await get_branch(chat_id, target_branch_name, session)

    if not source_branch or not target_branch:
        raise BranchNotFoundError("Исходная или целевая ветка не найдена.")

    source_head_id = source_branch.head_message_id
    source_last_merged_id = source_branch.last_merged_message_id
    target_head_id = target_branch.head_message_id
    target_last_merged_id = target_branch.last_merged_message_id
    start_node_id = source_last_merged_id or source_branch.forked_from_id

    if target_head_id is not None and not await message_belongs_to_chat(chat_id, target_head_id, session):
        raise GraphIntegrityError(f"Некорректная вершина целевой ветки в чате {chat_id}.")
    delta = await _read_history(chat_id, source_head_id, session, stop_id=start_node_id)
    active_delta = [msg for msg in reversed(delta) if msg.is_active]
    if not active_delta or source_head_id is None:
        raise NoActiveMessagesToMergeError(
            "Отсутствуют активные сообщения для слияния."
        )

    previous = (
        await get_latest_merge_event(chat_id, source_branch_name, session)
        if source_last_merged_id is not None else None
    )
    previous_summary = previous.content if previous is not None and previous.is_active else None
    summary_text = await summarizer_func(active_delta, previous_summary)
    if not isinstance(summary_text, str) or not summary_text.strip():
        raise InvalidMergeError("Суммаризатор вернул пустую или некорректную выжимку.")

    await reserve_branch_write(
        session, source_branch.id, source_head_id, source_last_merged_id,
    )
    await reserve_branch_write(
        session, target_branch.id, target_head_id, target_last_merged_id,
    )

    merge_msg = MergeEvent(
        id=str(uuid.uuid4()),
        chat_id=chat_id,
        parent_id=target_head_id,
        role=MessageRole.SYSTEM_MERGE,
        content=summary_text,
        is_active=True,
        source_branch_name=source_branch_name,
        forked_from_msg_id=start_node_id or target_head_id or source_head_id,
        compressed_messages_count=len(active_delta),
        source_head_message_id=source_head_id,
    )
    session.add(merge_msg)
    await session.flush()

    target_branch.head_message_id = merge_msg.id
    source_branch.last_merged_message_id = source_head_id

    await session.flush()

    return {
        "status": "success",
        "merge_message_id": merge_msg.id,
        "source_branch": source_branch_name,
        "target_branch": target_branch_name,
        "compressed_messages_count": len(active_delta),
        "summary": summary_text,
    }
