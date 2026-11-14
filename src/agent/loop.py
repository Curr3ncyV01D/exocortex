import asyncio
import json
import logging
import time
from collections.abc import Callable
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.agent.client import GeminiClient, map_messages_to_gemini
from src.agent.prompts import FALLBACK_MAX_ITERATIONS_PROMPT
from src.agent.tools import get_branch_summary, search_history_fts
from src.storage.crud.chats import append_message, get_branch
from src.storage.crud.graph import BranchNotFoundError, read_context_from_head
from src.storage.models import Message, MessageRole

_branch_locks: dict[str, asyncio.Lock] = {}
logger = logging.getLogger(__name__)


class AgentLoopError(Exception):
    """Базовое исключение исполнительного цикла агента."""


class BranchBusyError(AgentLoopError):
    """Исключение: ветка заблокирована другой активной генерацией."""


class LLMGenerationError(AgentLoopError):
    """Исключение: сбой генерации внешнего API или блокировка ответа."""


async def run_turn(
    chat_id: str,
    branch_name: str,
    client: GeminiClient,
    session: AsyncSession,
    user_prompt: Optional[str] = None,
    max_iterations: int = 3,
) -> Message:
    """Выполняет ход в выделенной сессии и отдельно фиксирует вопрос и ответ."""
    if session.in_transaction():
        raise ValueError("Для генерации требуется выделенная сессия без открытой транзакции.")
    branch = await get_branch(chat_id=chat_id, branch_name=branch_name, session=session)
    if not branch:
        raise BranchNotFoundError(
            f"Ветка '{branch_name}' не найдена в чате {chat_id}"
        )

    lock = _branch_locks.setdefault(branch.id, asyncio.Lock())
    if lock.locked():
        raise BranchBusyError(
            f"Ветка '{branch_name}' занята выполнением другого запроса."
        )

    async with lock:
        head_id = branch.head_message_id
        if user_prompt is not None and user_prompt.strip():
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

        timeline = await read_context_from_head(chat_id, head_id, session)
        active_ids = [msg.id for msg in timeline]
        contents = map_messages_to_gemini(timeline)

        async def search_history(query: str, limit: int = 5) -> str:
            """Выполняет поиск по историческим сообщениям текущего чата."""
            return await search_history_fts(
                chat_id=chat_id,
                query=query,
                exclude_message_ids=active_ids,
                session=session,
                limit=limit,
            )

        async def get_summary(branch_name: str) -> str:
            """Возвращает концентрированный итог слияния указанной ветки."""
            return await get_branch_summary(
                chat_id=chat_id,
                branch_name=branch_name,
                session=session,
            )

        tool_registry: dict[str, Callable[..., Any]] = {
            "search_history": search_history,
            "get_summary": get_summary,
        }
        tools_list = [search_history, get_summary]

        loop_start = time.perf_counter()
        execution_steps: list[dict[str, Any]] = []
        seen_calls: set[tuple[str, str]] = set()
        iterations_used = 0
        final_text: Optional[str] = None

        for _ in range(max_iterations):
            try:
                response = await client.generate_step(
                    contents=contents,
                    tools=tools_list,
                )
            except Exception as exc:
                raise LLMGenerationError(
                    f"Сбой вызова внешнего API Gemini: {exc}"
                ) from exc

            function_calls = client.extract_function_calls(response)
            if not function_calls:
                final_text = client.extract_response_text(response)
                break

            iterations_used += 1
            tool_results: list[tuple[str, str]] = []

            for tool_name, tool_args in function_calls:
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
                tool_results.append((tool_name, output_str))

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

            client.append_tool_interaction(contents, response, tool_results)

        if final_text is None:
            client.append_user_text(contents, FALLBACK_MAX_ITERATIONS_PROMPT)
            try:
                fallback_resp = await client.generate_step(
                    contents=contents,
                    tools=None,
                )
                final_text = client.extract_response_text(fallback_resp)
            except Exception as exc:
                raise LLMGenerationError(
                    f"Сбой вызова Gemini на этапе принудительного завершения: {exc}"
                ) from exc

        if not final_text:
            raise LLMGenerationError(
                "Модель вернула пустой ответ или генерация заблокирована фильтрами."
            )

        total_duration_ms = round((time.perf_counter() - loop_start) * 1000, 2)
        trace_payload = {
            "execution_trace": {
                "iterations_used": iterations_used,
                "steps": execution_steps,
                "total_duration_ms": total_duration_ms,
            }
        }

        try:
            answer = await append_message(
                chat_id=chat_id,
                branch_name=branch_name,
                role=MessageRole.MODEL,
                content=final_text,
                metadata_json=json.dumps(trace_payload, ensure_ascii=False),
                session=session,
                expected_head_id=head_id,
            )
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
        return answer
