import asyncio
import json
import uuid
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.genai import types
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent.client import GeminiClient
from src.agent.loop import BranchBusyError, LLMGenerationError, run_turn
from src.storage.crud.chats import append_message
from src.storage.crud.graph import branch_from_here
from src.storage.models import Branch, Message, MessageRole


def _make_text_response(text: Optional[str]) -> MagicMock:
    """Создает мок-ответ SDK с финальным текстовым сообщением."""
    resp = MagicMock(spec=types.GenerateContentResponse)
    resp.function_calls = []
    resp.text = text
    content = (
        types.Content(role="model", parts=[types.Part.from_text(text=text)])
        if text is not None
        else None
    )
    resp.candidates = [MagicMock(content=content)] if content else []
    return resp


def _make_tool_call_response(
    calls: list[tuple[str, dict[str, Any]]],
) -> MagicMock:
    """Создает мок-ответ SDK с запросом на вызов одного или нескольких инструментов."""
    resp = MagicMock(spec=types.GenerateContentResponse)
    call_mocks = []
    parts = []
    for name, args in calls:
        c = MagicMock()
        c.name = name
        c.args = args
        call_mocks.append(c)
        parts.append(types.Part.from_function_call(name=name, args=args))

    resp.function_calls = call_mocks
    resp.text = None
    resp.candidates = [
        MagicMock(content=types.Content(role="model", parts=parts))
    ]
    return resp


@pytest.mark.asyncio
async def test_run_turn_direct_response_happy_path(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка прямого ответа модели без вызова инструментов и записи трассировки."""
    chat_id = initial_chat_setup["chat_id"]

    with patch("src.agent.client.genai.Client"):
        client = GeminiClient(api_key="test-key")
        client.generate_step = AsyncMock(
            return_value=_make_text_response("Рациональный ответ ментора.")
        )

        model_msg = await run_turn(
            chat_id=chat_id,
            branch_name="main",
            client=client,
            session=session,
            user_prompt="В чем суть проблемы?",
        )

    assert model_msg.role == MessageRole.MODEL
    assert model_msg.content == "Рациональный ответ ментора."
    assert model_msg.metadata_json is not None

    meta = json.loads(model_msg.metadata_json)
    assert meta["execution_trace"]["iterations_used"] == 0
    assert meta["execution_trace"]["steps"] == []
    assert meta["execution_trace"]["total_duration_ms"] >= 0

    stmt = select(Branch).where(Branch.chat_id == chat_id, Branch.name == "main")
    branch = (await session.execute(stmt)).scalar_one()
    assert branch.head_message_id == model_msg.id


@pytest.mark.asyncio
async def test_run_turn_with_tool_call_and_real_fts_search(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка полного цикла ReAct: вызов search_history, чтение БД и трассировка."""
    chat_id = initial_chat_setup["chat_id"]
    root_id = initial_chat_setup["root_message_id"]

    await branch_from_here(
        chat_id=chat_id,
        target_message_id=root_id,
        new_branch_name="archive-branch",
        session=session,
    )
    await append_message(
        chat_id=chat_id,
        branch_name="archive-branch",
        role=MessageRole.USER,
        content="Исторический факт: проект написан на FastAPI.",
        session=session,
    )

    with patch("src.agent.client.genai.Client"):
        client = GeminiClient(api_key="test-key")
        client.generate_step = AsyncMock(
            side_effect=[
                _make_tool_call_response([("search_history", {"query": "FastAPI"})]),
                _make_text_response("Нашел в истории: проект написан на FastAPI."),
            ]
        )

        model_msg = await run_turn(
            chat_id=chat_id,
            branch_name="main",
            client=client,
            session=session,
            user_prompt="На чем написан проект?",
        )

    assert client.generate_step.await_count == 2
    assert "FastAPI" in model_msg.content

    meta = json.loads(model_msg.metadata_json or "{}")
    trace = meta["execution_trace"]
    assert trace["iterations_used"] == 1
    assert len(trace["steps"]) == 1
    assert trace["steps"][0]["tool_called"] == "search_history"
    assert trace["steps"][0]["arguments"] == {"query": "FastAPI"}
    assert "FastAPI" in trace["steps"][0]["output_summary"]


@pytest.mark.asyncio
async def test_run_turn_loop_guard_blocks_duplicate_calls(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка срабатывания Loop Guard при попытке повторного вызова тула с теми же аргументами."""
    chat_id = initial_chat_setup["chat_id"]

    with patch("src.agent.client.genai.Client"):
        client = GeminiClient(api_key="test-key")
        client.generate_step = AsyncMock(
            side_effect=[
                _make_tool_call_response([("search_history", {"query": "дубль"})]),
                _make_tool_call_response([("search_history", {"query": "дубль"})]),
                _make_text_response("Окей, понял ошибку и отвечаю."),
            ]
        )

        model_msg = await run_turn(
            chat_id=chat_id,
            branch_name="main",
            client=client,
            session=session,
            user_prompt="Проверь повторы",
        )

    assert client.generate_step.await_count == 3
    meta = json.loads(model_msg.metadata_json or "{}")
    steps = meta["execution_trace"]["steps"]
    assert len(steps) == 2
    assert "заблокирован" in steps[1]["output_summary"].lower()


@pytest.mark.asyncio
async def test_run_turn_max_iterations_fallback(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка принудительного отключения тулов (Fallback) при превышении лимита шагов."""
    chat_id = initial_chat_setup["chat_id"]

    with patch("src.agent.client.genai.Client"):
        client = GeminiClient(api_key="test-key")
        client.generate_step = AsyncMock(
            side_effect=[
                _make_tool_call_response([("get_summary", {"branch_name": "b1"})]),
                _make_tool_call_response([("get_summary", {"branch_name": "b2"})]),
                _make_tool_call_response([("get_summary", {"branch_name": "b3"})]),
                _make_text_response("Принудительный финальный ответ без тулов."),
            ]
        )

        model_msg = await run_turn(
            chat_id=chat_id,
            branch_name="main",
            client=client,
            session=session,
            user_prompt="Сложный запрос",
            max_iterations=3,
        )

    assert client.generate_step.await_count == 4
    fourth_call_kwargs = client.generate_step.call_args_list[3].kwargs
    assert fourth_call_kwargs.get("tools") is None
    assert model_msg.content == "Принудительный финальный ответ без тулов."


@pytest.mark.asyncio
async def test_run_turn_api_failure_preserves_user_message(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка Варианта Б: при сбое Gemini сообщение пользователя сохраняется в БД."""
    chat_id = initial_chat_setup["chat_id"]

    with patch("src.agent.client.genai.Client"):
        client = GeminiClient(api_key="test-key")
        client.generate_step = AsyncMock(
            side_effect=RuntimeError("503 Gemini Service Unavailable")
        )

        with pytest.raises(LLMGenerationError):
            await run_turn(
                chat_id=chat_id,
                branch_name="main",
                client=client,
                session=session,
                user_prompt="Важная надиктованная мысль",
            )

    stmt = select(Branch).where(Branch.chat_id == chat_id, Branch.name == "main")
    branch = (await session.execute(stmt)).scalar_one()
    head_msg = await session.get(Message, branch.head_message_id)
    assert head_msg is not None
    assert head_msg.role == MessageRole.USER
    assert head_msg.content == "Важная надиктованная мысль"


@pytest.mark.asyncio
async def test_run_turn_retry_without_prompt_uses_existing_head(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка режима Rerun / отправки цепочки: генерация при user_prompt=None."""
    chat_id = initial_chat_setup["chat_id"]

    msg_u2 = await append_message(
        chat_id=chat_id,
        branch_name="main",
        role=MessageRole.USER,
        content="Второе сообщение цепочки",
        session=session,
    )

    with patch("src.agent.client.genai.Client"):
        client = GeminiClient(api_key="test-key")
        client.generate_step = AsyncMock(
            return_value=_make_text_response("Ответ на всю цепочку")
        )

        model_msg = await run_turn(
            chat_id=chat_id,
            branch_name="main",
            client=client,
            session=session,
            user_prompt=None,
        )

    assert model_msg.parent_id == msg_u2.id
    call_contents = client.generate_step.call_args.kwargs["contents"]
    assert len(call_contents) == 1
    assert call_contents[0].role == "user"
    assert len(call_contents[0].parts) == 2


@pytest.mark.asyncio
async def test_run_turn_empty_or_blocked_model_response_raises_error(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка защиты БД от сохранения пустого или заблокированного ответа."""
    chat_id = initial_chat_setup["chat_id"]

    with patch("src.agent.client.genai.Client"):
        client = GeminiClient(api_key="test-key")
        client.generate_step = AsyncMock(return_value=_make_text_response(None))

        with pytest.raises(LLMGenerationError):
            await run_turn(
                chat_id=chat_id,
                branch_name="main",
                client=client,
                session=session,
                user_prompt="Опасный запрос",
            )

    stmt = select(Branch).where(Branch.chat_id == chat_id, Branch.name == "main")
    branch = (await session.execute(stmt)).scalar_one()
    head_msg = await session.get(Message, branch.head_message_id)
    assert head_msg.role == MessageRole.USER


@pytest.mark.asyncio
async def test_run_turn_unknown_tool_graceful_recovery(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка устойчивости цикла при галлюцинации несуществующего имени инструмента."""
    chat_id = initial_chat_setup["chat_id"]

    with patch("src.agent.client.genai.Client"):
        client = GeminiClient(api_key="test-key")
        client.generate_step = AsyncMock(
            side_effect=[
                _make_tool_call_response([("hallucinated_tool", {"x": 1})]),
                _make_text_response("Восстановился после ошибки инструмента."),
            ]
        )

        model_msg = await run_turn(
            chat_id=chat_id,
            branch_name="main",
            client=client,
            session=session,
            user_prompt="Вызови что-нибудь",
        )

    assert model_msg.content == "Восстановился после ошибки инструмента."
    meta = json.loads(model_msg.metadata_json or "{}")
    assert "неизвестный инструмент" in meta["execution_trace"]["steps"][0]["output_summary"].lower()


@pytest.mark.asyncio
async def test_run_turn_concurrent_branch_lock_raises_busy(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка защиты ветки от одновременного запуска двух генераций (BranchBusyError)."""
    chat_id = initial_chat_setup["chat_id"]
    generation_started = asyncio.Event()
    allow_finish = asyncio.Event()

    async def slow_generate(*args: Any, **kwargs: Any) -> MagicMock:
        generation_started.set()
        await allow_finish.wait()
        return _make_text_response("Ответ первого запроса")

    with patch("src.agent.client.genai.Client"):
        client = GeminiClient(api_key="test-key")
        client.generate_step = AsyncMock(side_effect=slow_generate)

        task1 = asyncio.create_task(
            run_turn(
                chat_id=chat_id,
                branch_name="main",
                client=client,
                session=session,
                user_prompt="Первый запрос",
            )
        )
        await generation_started.wait()

        with pytest.raises(BranchBusyError):
            await run_turn(
                chat_id=chat_id,
                branch_name="main",
                client=client,
                session=session,
                user_prompt="Второй параллельный запрос",
            )

        allow_finish.set()
        await task1