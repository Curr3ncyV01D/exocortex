from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent.client import GeminiClient
from src.agent.loop import LLMGenerationError, run_turn
from src.storage.crud.chats import append_message
from src.storage.crud.graph import (
    BranchConflictError,
    BranchNotFoundError,
    GraphEngineError,
    MergeResult,
    MessageNotFoundError,
    branch_from_here,
    read_context_from_head,
    reserve_branch_write,
    squash_merge_branch,
    toggle_message_active,
    update_message_content,
)
from src.storage.models import Branch, Chat, Message, MessageRole


class ChatNotFoundError(GraphEngineError):
    """Запрошенный чат отсутствует."""


class AncestorMutationWarning(GraphEngineError):
    def __init__(self, affected_branch_count: int, message: str) -> None:
        super().__init__(message)
        self.affected_branch_count = affected_branch_count


class RegenerationFailedError(GraphEngineError):
    def __init__(
        self, branch_id: str, head_message_id: str, generation_error: LLMGenerationError,
    ) -> None:
        super().__init__(str(generation_error))
        self.branch_id = branch_id
        self.head_message_id = head_message_id
        self.generation_error = generation_error


async def _require_chat(session: AsyncSession, chat_id: str) -> None:
    if await session.get(Chat, chat_id) is None:
        raise ChatNotFoundError(f"Чат {chat_id} не найден.")


async def _require_branch(session: AsyncSession, branch_id: str) -> Branch:
    branch = await session.get(Branch, branch_id)
    if branch is None:
        raise BranchNotFoundError(f"Ветка {branch_id} не найдена.")
    return branch


async def create_chat(session: AsyncSession, title: str) -> Chat:
    async with session.begin():
        chat = Chat(title=title)
        session.add(chat)
        await session.flush()
        session.add(Branch(chat_id=chat.id, name="main"))
    return chat


async def list_chats(session: AsyncSession) -> list[Chat]:
    return list(await session.scalars(select(Chat).order_by(Chat.updated_at.desc(), Chat.id)))


async def list_branches(session: AsyncSession, chat_id: str) -> list[Branch]:
    await _require_chat(session, chat_id)
    return list(await session.scalars(
        select(Branch).where(Branch.chat_id == chat_id).order_by(Branch.created_at, Branch.id)
    ))


async def create_branch(
    session: AsyncSession, chat_id: str, target_message_id: str, name: str,
) -> Branch:
    async with session.begin():
        await _require_chat(session, chat_id)
        return await branch_from_here(chat_id, target_message_id, name, session)


async def get_timeline(session: AsyncSession, branch_id: str) -> list[Message]:
    branch = await _require_branch(session, branch_id)
    return await read_context_from_head(branch.chat_id, branch.head_message_id, session)


async def send_message(session: AsyncSession, branch_id: str, content: str) -> Message:
    async with session.begin():
        branch = await _require_branch(session, branch_id)
        return await append_message(branch.chat_id, branch.name, MessageRole.USER, content, session)


async def generate_reply(
    session: AsyncSession, client: GeminiClient, branch_id: str, expected_head_id: str | None,
) -> Message:
    async with session.begin():
        branch = await _require_branch(session, branch_id)
        chat_id, name, head_id = branch.chat_id, branch.name, branch.head_message_id
        if expected_head_id is not None and head_id != expected_head_id:
            raise BranchConflictError("Состояние ветки изменилось. Повторите операцию.")
        head = await session.get(Message, head_id) if head_id is not None else None
        if head is None or head.role != MessageRole.USER or not head.is_active:
            raise BranchConflictError("На вершине ветки нет активной реплики пользователя.")

    # Даже без клиентского ID привязываем генерацию к проверенному вопросу.
    return await run_turn(chat_id, name, client, session, expected_head_id=head_id)


async def regenerate_reply(
    session: AsyncSession,
    client: GeminiClient,
    branch_id: str,
    expected_head_id: str,
    new_branch_name: str,
) -> tuple[Branch, Message]:
    async with session.begin():
        source = await _require_branch(session, branch_id)
        if source.head_message_id != expected_head_id:
            raise BranchConflictError("Состояние ветки изменилось. Повторите операцию.")
        await reserve_branch_write(session, source.id, expected_head_id, source.last_merged_message_id)
        head = await session.get(Message, expected_head_id)
        parent = (
            await session.get(Message, head.parent_id)
            if head is not None and head.parent_id is not None else None
        )
        if (
            head is None or head.role != MessageRole.MODEL or parent is None
            or parent.role != MessageRole.USER or not parent.is_active
        ):
            raise BranchConflictError("Для перегенерации нужен ответ модели на активную реплику пользователя.")
        chat_id, question_id = source.chat_id, parent.id
        fork = await branch_from_here(chat_id, question_id, new_branch_name, session)
        fork_id = fork.id

    try:
        answer = await run_turn(chat_id, new_branch_name, client, session, expected_head_id=question_id)
    except LLMGenerationError as exc:
        raise RegenerationFailedError(fork_id, question_id, exc) from exc
    return await _require_branch(session, fork_id), answer


async def _summarize(client: GeminiClient, delta: list[Message], previous_summary: str | None) -> str:
    try:
        summary = await client.summarize_delta(delta, previous_summary)
    except Exception as exc:
        raise LLMGenerationError("Сбой суммаризации Gemini.") from exc
    if not summary.strip():
        raise LLMGenerationError("Gemini не вернул завершённую выжимку.")
    return summary


async def merge_branch(
    session: AsyncSession, client: GeminiClient, branch_id: str, target_branch: str,
) -> MergeResult:
    async def summarize(delta: list[Message], previous_summary: str | None) -> str:
        return await _summarize(client, delta, previous_summary)

    async with session.begin():
        branch = await _require_branch(session, branch_id)
        return await squash_merge_branch(
            branch.chat_id, branch.name, target_branch, summarizer_func=summarize, session=session,
        )


async def edit_message(session: AsyncSession, message_id: str, content: str, force: bool) -> Message:
    async with session.begin():
        result = await update_message_content(message_id, content, force, session=session)
        if result["status"] == "warning_required":
            raise AncestorMutationWarning(result["affected_branch_count"], result["message"])
        message = await session.get(Message, message_id)
        if message is None:
            raise MessageNotFoundError(f"Сообщение {message_id} не найдено.")
    return message


async def toggle_message(session: AsyncSession, message_id: str) -> dict[str, str | bool]:
    async with session.begin():
        is_active = await toggle_message_active(message_id, session)
    return {"message_id": message_id, "is_active": is_active}
