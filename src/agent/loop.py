import asyncio
import json
import logging
import time
from collections.abc import Callable
from typing import Any, Optional

from google.genai import types
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent.client import GeminiClient, ToolCall, map_messages_to_gemini
from src.agent.prompts import FALLBACK_MAX_ITERATIONS_PROMPT
from src.agent.tools import get_branch_summary, search_history_fts
from src.storage.crud.chats import append_message
from src.storage.crud.graph import BranchNotFoundError, get_branch, read_context_from_head
from src.storage.models import Message, MessageRole

_branch_locks: dict[str, asyncio.Lock] = {}
logger = logging.getLogger(__name__)
TURN_TIMEOUT_SECONDS = 90
MAX_TOOL_CALLS = 12
MAX_CONTEXT_BYTES = 512_000


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
    if user_prompt is not None and not user_prompt.strip():
        raise ValueError("Промпт должен содержать текст.")
    try:
        async with asyncio.timeout(TURN_TIMEOUT_SECONDS):
            return await _run_turn(chat_id, branch_name, client, session, user_prompt, max_iterations)
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
) -> Message:
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
        if user_prompt is not None:
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
        if not timeline:
            raise LLMGenerationError("В ветке нет активных реплик для генерации.")
        active_ids = [msg.id for msg in timeline]
        contents = map_messages_to_gemini(timeline)
        await session.rollback()

        async def generate(
            tools: list[Callable[..., Any]] | None,
        ) -> types.GenerateContentResponse:
            context_size = sum(len(item.model_dump_json().encode("utf-8")) for item in contents)
            if context_size > MAX_CONTEXT_BYTES:
                raise LLMGenerationError("Контекст хода превышает допустимый размер.")
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
                    finally:
                        if session.in_transaction():
                            await session.rollback()

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

            client.append_tool_interaction(contents, response, tool_results)

        if final_text is None:
            client.append_user_text(contents, FALLBACK_MAX_ITERATIONS_PROMPT)
            final_text = client.extract_response_text(await generate(None))

        if not final_text:
            raise LLMGenerationError(
                "Модель не вернула завершённый текстовый ответ."
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
