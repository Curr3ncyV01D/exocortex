import asyncio
import json
import logging
import time
from bisect import bisect_right
from collections.abc import Callable
from typing import Any, Optional

from google.genai import types
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent.client import GeminiClient, ToolCall, map_messages_to_gemini
from src.agent.prompts import FALLBACK_MAX_ITERATIONS_PROMPT
from src.agent.tools import get_branch_summary, search_history_fts
from src.config import settings
from src.storage.crud.chats import append_message
from src.storage.crud.graph import (
    BranchConflictError,
    BranchNotFoundError,
    get_branch,
    latest_dialogue_message,
    read_context_from_head,
)
from src.storage.models import Message, MessageRole

_branch_locks: dict[str, asyncio.Lock] = {}
logger = logging.getLogger(__name__)
TURN_TIMEOUT_SECONDS = 90
MAX_TOOL_CALLS = 12


class AgentLoopError(Exception):
    """Базовое исключение исполнительного цикла агента."""


class BranchBusyError(AgentLoopError):
    """Исключение: ветка заблокирована другой активной генерацией."""


class LLMGenerationError(AgentLoopError):
    """Исключение: сбой генерации внешнего API или блокировка ответа."""


class PromptTooLargeError(LLMGenerationError):
    """Последняя реплика превышает бюджет контекста."""


def _contents_size(contents: list[types.Content]) -> int:
    return sum(len(item.model_dump_json().encode("utf-8")) for item in contents)


def _check_latest_message(message: Message, budget: int) -> list[types.Content]:
    contents = map_messages_to_gemini([message])
    if _contents_size(contents) > budget:
        raise PromptTooLargeError(
            f"Реплика превышает MAX_CONTEXT_BYTES ({budget} байт). "
            "Разбейте сообщение или увеличьте MAX_CONTEXT_BYTES в настройках."
        )
    return contents


def _context_window(
    timeline: list[Message], budget: int, question_index: int,
) -> tuple[list[types.Content], list[str]]:
    question = timeline[question_index]
    contents = _check_latest_message(question, budget)
    included = len(timeline) - question_index
    if included > 1:
        contents = map_messages_to_gemini(timeline[question_index:])
        if _contents_size(contents) > budget:
            # If the question and all newer summaries do not fit, keep the question
            # and as many of the newest summaries as the budget permits.
            def with_summaries(count: int) -> list[Message]:
                return [question, *timeline[len(timeline) - count:]]

            count = bisect_right(
                range(included), budget,
                key=lambda size: _contents_size(map_messages_to_gemini(with_summaries(size))),
            ) - 1
            window = with_summaries(count)
            return map_messages_to_gemini(window), [msg.id for msg in window]
    # Стоимость растёт с длиной суффикса даже после группировки ролей.
    # Сначала ограничиваем поиск последними репликами, затем уточняем границу.
    candidate = included * 2
    while candidate <= len(timeline):
        mapped = map_messages_to_gemini(timeline[-candidate:])
        if _contents_size(mapped) > budget:
            break
        contents, included = mapped, candidate
        candidate *= 2

    upper = min(len(timeline), candidate - 1)
    while included < upper:
        count = (included + upper + 1) // 2
        mapped = map_messages_to_gemini(timeline[-count:])
        if _contents_size(mapped) <= budget:
            contents, included = mapped, count
        else:
            upper = count - 1

    window = timeline[-included:]
    if contents[0].role == "model":
        first_user = next(
            (index for index, msg in enumerate(window) if msg.role != MessageRole.MODEL),
            len(window),
        )
        window = window[first_user:]
        contents = contents[1:]
    if not contents:
        raise LLMGenerationError("В окне контекста нет реплики пользователя или сводки.")
    return contents, [msg.id for msg in window]


async def run_turn(
    chat_id: str,
    branch_name: str,
    client: GeminiClient,
    session: AsyncSession,
    user_prompt: Optional[str] = None,
    max_iterations: int = 3,
    *,
    expected_head_id: Optional[str] = None,
    expected_question_id: Optional[str] = None,
) -> Message:
    """Выполняет ход в выделенной сессии и отдельно фиксирует вопрос и ответ."""
    if session.in_transaction():
        raise ValueError("Для генерации требуется выделенная сессия без открытой транзакции.")
    if user_prompt is not None and not user_prompt.strip():
        raise ValueError("Промпт должен содержать текст.")
    try:
        async with asyncio.timeout(TURN_TIMEOUT_SECONDS):
            return await _run_turn(
                chat_id, branch_name, client, session, user_prompt, max_iterations,
                expected_head_id, expected_question_id,
            )
    except TimeoutError as exc:
        raise LLMGenerationError("Истёк лимит времени на генерацию хода.") from exc
    finally:
        if session.in_transaction():
            await session.rollback()


async def _run_turn(
    chat_id: str,
    branch_name: str,
    client: GeminiClient,
    session: AsyncSession,
    user_prompt: str | None,
    max_iterations: int,
    expected_head_id: str | None,
    expected_question_id: str | None,
) -> Message:
    branch = await get_branch(chat_id, branch_name, session, refresh=True)
    if not branch:
        raise BranchNotFoundError(
            f"Ветка '{branch_name}' не найдена в чате {chat_id}"
        )

    head_id = branch.head_message_id
    if expected_head_id is not None and head_id != expected_head_id:
        raise BranchConflictError("Состояние ветки изменилось. Повторите операцию.")

    lock = _branch_locks.setdefault(branch.id, asyncio.Lock())
    if lock.locked():
        raise BranchBusyError(
            f"Ветка '{branch_name}' занята выполнением другого запроса."
        )

    async with lock:
        budget = settings.MAX_CONTEXT_BYTES
        if user_prompt is not None:
            _check_latest_message(Message(role=MessageRole.USER, content=user_prompt), budget)
            try:
                question = await append_message(
                    chat_id=chat_id,
                    branch_name=branch_name,
                    role=MessageRole.USER,
                    content=user_prompt,
                    session=session,
                    expected_head_id=head_id,
                )
                head_id = question.id
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

        await session.rollback()

        async with AsyncSession(bind=session.bind, autoflush=False) as snapshot:
            # SQLite's legacy transaction mode does not begin a snapshot for SELECT.
            await snapshot.execute(text("BEGIN"))
            snapshot_branch = await get_branch(chat_id, branch_name, snapshot)
            if snapshot_branch is None or snapshot_branch.head_message_id != head_id:
                raise BranchConflictError("Состояние ветки изменилось. Повторите операцию.")
            timeline = await read_context_from_head(chat_id, head_id, snapshot)
            question = latest_dialogue_message(timeline)
            if question is None or question.role != MessageRole.USER:
                raise BranchConflictError("В ветке нет активного ожидающего вопроса.")
            question_id = question.id
            if expected_question_id is not None and question_id != expected_question_id:
                raise BranchConflictError("Ожидающий вопрос изменился. Проверьте историю ветки.")
            contents, active_ids = _context_window(timeline, budget, timeline.index(question))
            final_text, trace = await _generate_from_snapshot(
                chat_id, client, snapshot, contents, active_ids, budget, max_iterations,
            )

        reply_metadata = {"reply_to_message_id": question_id, "execution_trace": trace}
        try:
            answer = await append_message(
                chat_id=chat_id,
                branch_name=branch_name,
                role=MessageRole.MODEL,
                content=final_text,
                metadata_json=json.dumps(reply_metadata, ensure_ascii=False),
                session=session,
                expected_head_id=head_id,
            )
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
        return answer


async def _generate_from_snapshot(
    chat_id: str,
    client: GeminiClient,
    snapshot: AsyncSession,
    contents: list[types.Content],
    active_ids: list[str],
    budget: int,
    max_iterations: int,
) -> tuple[str, dict[str, Any]]:
    async def generate(
        tools: list[Callable[..., Any]] | None,
    ) -> types.GenerateContentResponse:
        try:
            return await client.generate_step(contents=contents, tools=tools)
        except TimeoutError:
            raise
        except Exception as exc:
            raise LLMGenerationError("Сбой вызова Gemini.") from exc

    async def search_history(query: str, limit: int = 5) -> str:
        """Выполняет поиск по историческим сообщениям текущего чата."""
        return await search_history_fts(
            chat_id=chat_id,
            query=query,
            exclude_message_ids=active_ids,
            session=snapshot,
            limit=limit,
        )

    async def get_summary(branch_name: str) -> str:
        """Возвращает концентрированный итог слияния указанной ветки."""
        return await get_branch_summary(
            chat_id=chat_id,
            branch_name=branch_name,
            session=snapshot,
        )

    tools_list = [search_history, get_summary]
    tool_registry: dict[str, Callable[..., Any]] = {
        tool.__name__: tool for tool in tools_list
    }

    loop_start = time.perf_counter()
    execution_steps: list[dict[str, Any]] = []
    seen_calls: set[tuple[str, str]] = set()
    iterations_used = 0
    final_text: Optional[str] = None

    for _ in range(max_iterations):
        response = await generate(tools_list)
        function_calls = client.extract_function_calls(response)
        if not function_calls:
            final_text = client.extract_response_text(response)
            break
        if len(execution_steps) + len(function_calls) > MAX_TOOL_CALLS:
            break

        iterations_used += 1
        tool_results: list[tuple[ToolCall, str]] = []

        for call in function_calls:
            tool_name, tool_args = call.name, call.arguments
            step_start = time.perf_counter()
            call_sig = (
                tool_name,
                json.dumps(tool_args, sort_keys=True, ensure_ascii=False),
            )

            if call_sig in seen_calls:
                output_str = (
                    "ОШИБКА: Повторный вызов инструмента с идентичными "
                    "аргументами заблокирован. Измени запрос или ответь пользователю."
                )
            elif tool_name not in tool_registry:
                output_str = f"Ошибка: неизвестный инструмент '{tool_name}'."
            else:
                seen_calls.add(call_sig)
                try:
                    output_str = await tool_registry[tool_name](**tool_args)
                except Exception:
                    logger.exception("Сбой инструмента %s", tool_name)
                    output_str = "Ошибка выполнения инструмента. Продолжи без его результата."

            step_duration_ms = round((time.perf_counter() - step_start) * 1000, 2)
            tool_results.append((call, output_str))

            summary_preview = (
                output_str[:300] + "..."
                if len(output_str) > 300
                else output_str
            )
            execution_steps.append(
                {
                    "step": len(execution_steps) + 1,
                    "tool_called": tool_name,
                    "arguments": tool_args,
                    "output_summary": summary_preview,
                    "execution_time_ms": step_duration_ms,
                }
            )

        next_contents = contents.copy()
        client.append_tool_interaction(next_contents, response, tool_results)
        if _contents_size(next_contents) > budget:
            break
        contents = next_contents

    if final_text is None:
        fallback_contents = [*contents[:-1], contents[-1].model_copy(deep=True)]
        client.append_user_text(fallback_contents, FALLBACK_MAX_ITERATIONS_PROMPT)
        if _contents_size(fallback_contents) <= budget:
            contents = fallback_contents
        final_text = client.extract_response_text(await generate(None))

    if not final_text:
        raise LLMGenerationError(
            "Модель не вернула завершённый текстовый ответ."
        )

    total_duration_ms = round((time.perf_counter() - loop_start) * 1000, 2)
    return final_text, {
        "iterations_used": iterations_used,
        "steps": execution_steps,
        "total_duration_ms": total_duration_ms,
    }
