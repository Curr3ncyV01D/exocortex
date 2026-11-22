import json
import time
import asyncio
from uuid import uuid4
from hashlib import sha256

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent.client import GeminiClient
from src.agent.diagnostics import describe_failure, logger, operation_id, operation_scope
from src.config import settings
from src.agent.loop import LLMGenerationError, run_turn
from src.storage.crud.chats import append_message
from src.storage.crud.explorer import read_chat_graph
from src.schemas import BranchResponse, ChatGraphResponse, GraphMergeResponse, GraphNodeResponse
from src.storage.crud.graph import (
    BranchConflictError,
    BranchNotFoundError,
    GraphEngineError,
    GraphIntegrityError,
    MergeResult,
    MessageNotFoundError,
    branch_from_here,
    latest_dialogue_message,
    read_context_from_head,
    reserve_branch_write,
    squash_merge_branch,
    set_message_active,
    update_message_content,
)
from src.storage.models import Branch, Chat, Message, MessageRole, MessageSubmission


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


async def get_chat_graph(session: AsyncSession, chat_id: str) -> ChatGraphResponse:
    """Build a compact graph from one read snapshot without reserving SQLite's writer slot."""
    async with session.begin():
        # sqlite3 legacy transaction control does not start a transaction for SELECT.
        await session.execute(text("BEGIN"))
        snapshot = await read_chat_graph(session, chat_id)
        if snapshot is None:
            raise ChatNotFoundError(f"Чат {chat_id} не найден.")
        nodes = [GraphNodeResponse(
            **{key: value for key, value in node.items() if key != "content"},
            preview=" ".join(node["content"].split())[:200],
        ) for node in snapshot.messages]
        return ChatGraphResponse(
            chat_id=chat_id, nodes=nodes,
            branches=[BranchResponse.model_validate(branch) for branch in snapshot.branches],
            merges=[GraphMergeResponse.model_validate(merge) for merge in snapshot.merges],
        )


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


async def get_timeline(
    session: AsyncSession, branch_id: str, *, include_inactive: bool = False,
) -> list[Message]:
    branch = await _require_branch(session, branch_id)
    return await read_context_from_head(
        branch.chat_id, branch.head_message_id, session, include_inactive=include_inactive,
    )


async def send_message(
    session: AsyncSession, branch_id: str, content: str, idempotency_key: str | None = None,
) -> Message:
    async with session.begin():
        if idempotency_key is not None:
            # Acquire SQLite's writer slot before looking up the receipt.
            # A simultaneous retry will then read the committed result.
            await session.execute(text("BEGIN IMMEDIATE"))
        branch = await _require_branch(session, branch_id)
        if idempotency_key is None:
            return await append_message(branch.chat_id, branch.name, MessageRole.USER, content, session)

        content_hash = sha256(content.encode("utf-8")).hexdigest()
        receipt = await session.get(MessageSubmission, (branch_id, idempotency_key))
        if receipt is not None:
            if receipt.content_hash != content_hash:
                raise BranchConflictError("Этот ключ отправки уже использован для другого текста.")
            message = await session.get(Message, receipt.message_id)
            if message is None or message.chat_id != branch.chat_id:
                raise GraphIntegrityError("Запись об отправке не соответствует сохранённой реплике.")
            return message

        message = await append_message(branch.chat_id, branch.name, MessageRole.USER, content, session)
        session.add(MessageSubmission(
            branch_id=branch_id, key=idempotency_key, content_hash=content_hash, message_id=message.id,
        ))
        return message


async def generate_reply(
    session: AsyncSession, client: GeminiClient, branch_id: str, expected_head_id: str | None,
    expected_question_id: str | None = None,
) -> Message:
    async with session.begin():
        branch = await _require_branch(session, branch_id)
        chat_id, name, head_id = branch.chat_id, branch.name, branch.head_message_id
        if expected_head_id is not None and head_id != expected_head_id:
            raise BranchConflictError("Состояние ветки изменилось. Повторите операцию.")
    return await run_turn(
        chat_id, name, client, session, expected_head_id=head_id, expected_question_id=expected_question_id,
    )


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
        history = await read_context_from_head(source.chat_id, source.head_message_id, session, include_inactive=True)
        answer = latest_dialogue_message(history)
        if answer is None or answer.role != MessageRole.MODEL:
            raise BranchConflictError("Для перегенерации нужен последний активный ответ модели.")
        metadata = json.loads(answer.metadata_json or "{}") or {}
        question_id = metadata.get("reply_to_message_id", answer.parent_id)
        question = next((msg for msg in history[:history.index(answer)] if msg.id == question_id), None)
        if question is None or question.role != MessageRole.USER:
            raise BranchConflictError("Не найден исходный вопрос для перегенерации.")
        if not question.is_active:
            raise BranchConflictError("Исходный вопрос скрыт. Верните его в контекст для перегенерации.")
        chat_id, question_id = source.chat_id, question.id
        fork = await branch_from_here(chat_id, question_id, new_branch_name, session)
        fork_id = fork.id

    try:
        answer = await run_turn(
            chat_id, new_branch_name, client, session,
            expected_head_id=question_id, expected_question_id=question_id,
        )
    except LLMGenerationError as exc:
        raise RegenerationFailedError(fork_id, question_id, exc) from exc
    return await _require_branch(session, fork_id), answer


async def _summarize(client: GeminiClient, delta: list[Message], previous_summary: str | None) -> str:
    with operation_scope(uuid4().hex, settings.AGENT_OPERATION_TIMEOUT_SECONDS):
        return await _summarize_in_operation(client, delta, previous_summary)


async def _summarize_in_operation(client: GeminiClient, delta: list[Message], previous_summary: str | None) -> str:
    started_at = time.perf_counter()
    try:
        async with asyncio.timeout(settings.AGENT_OPERATION_TIMEOUT_SECONDS):
            summary = await client.summarize_delta(delta, previous_summary)
    except Exception as exc:
        logger.warning(
            "gemini_request_failed turn_id=%s phase=summarize model=%s messages=%d elapsed_ms=%.0f %s",
            operation_id.get(), client.model_name, len(delta), (time.perf_counter() - started_at) * 1000, describe_failure(exc),
        )
        raise LLMGenerationError("Сбой суммаризации Gemini.") from exc
    if not summary.strip():
        logger.warning("gemini_empty_response turn_id=%s phase=summarize model=%s messages=%d", operation_id.get(), client.model_name, len(delta))
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


async def set_message_active_state(session: AsyncSession, message_id: str, is_active: bool) -> dict[str, str | bool]:
    async with session.begin():
        is_active = await set_message_active(message_id, session, is_active=is_active)
    return {"message_id": message_id, "is_active": is_active}
