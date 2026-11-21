import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.exc import OperationalError
from google.genai import types
from google.genai.errors import ServerError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.agent.client import GeminiClient, map_messages_to_gemini
from src.agent.loop import BranchBusyError, LLMGenerationError, PromptTooLargeError, run_turn
from src.config import settings
from src.storage.crud.chats import append_message
from src.storage.crud.graph import (
    BranchConflictError, branch_from_here, squash_merge_branch,
    toggle_message_active, update_message_content,
)
from src.storage.models import Branch, Chat, MergeEvent, Message, MessageRole
from tests.conftest import create_initial_chat


def _make_text_response(text: Optional[str]) -> types.GenerateContentResponse:
    if text is None:
        return types.GenerateContentResponse()
    return types.GenerateContentResponse(candidates=[types.Candidate(
        finish_reason=types.FinishReason.STOP,
        content=types.Content(role="model", parts=[types.Part.from_text(text=text)]),
    )])


def _make_tool_call_response(
    calls: list[tuple[str, dict[str, Any]]],
) -> types.GenerateContentResponse:
    parts = [
        types.Part(function_call=types.FunctionCall(name=name, args=args, id=f"call-{index}"))
        for index, (name, args) in enumerate(calls)
    ]
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=parts),
    )])


@pytest.mark.asyncio
async def test_blank_prompt_does_not_call_gemini(session, initial_chat_setup):
    with patch("src.agent.client.genai.Client"):
        client = GeminiClient(api_key="test-key")
        client.generate_step = AsyncMock()
        with pytest.raises(ValueError, match="Промпт"):
            await run_turn(initial_chat_setup["chat_id"], "main", client, session, "  ")
        client.generate_step.assert_not_awaited()


@pytest.mark.asyncio
async def test_turn_deadline_releases_lock_and_keeps_question(
    session: AsyncSession, initial_chat_setup: dict[str, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat_id = initial_chat_setup["chat_id"]

    with patch("src.agent.client.genai.Client"):
        client = GeminiClient(api_key="test-key")

        async def slow_generation(**kwargs):
            await asyncio.sleep(1)

        client.generate_step = AsyncMock(side_effect=slow_generation)
        monkeypatch.setattr("src.agent.loop.TURN_TIMEOUT_SECONDS", 0.25)
        with pytest.raises(LLMGenerationError, match="лимит времени"):
            await run_turn(chat_id, "main", client, session, "Question")
        assert not session.in_transaction()
        monkeypatch.setattr("src.agent.loop.TURN_TIMEOUT_SECONDS", 90)
        client.generate_step = AsyncMock(return_value=_make_text_response("Recovered"))
        answer = await run_turn(chat_id, "main", client, session)
        question = await session.get(Message, answer.parent_id)
        assert question is not None and question.content == "Question"


@pytest.mark.asyncio
async def test_turn_budget_rejects_oversized_latest_message(session, initial_chat_setup, monkeypatch):
    monkeypatch.setattr(settings, "MAX_CONTEXT_BYTES", 1)
    with patch("src.agent.client.genai.Client"):
        client = GeminiClient(api_key="test-key")
        client.generate_step = AsyncMock()
        with pytest.raises(PromptTooLargeError, match="MAX_CONTEXT_BYTES"):
            await run_turn(initial_chat_setup["chat_id"], "main", client, session)
        client.generate_step.assert_not_awaited()
    assert not session.in_transaction()


@pytest.mark.asyncio
async def test_sliding_window_keeps_recent_messages_and_recovers_old_ones_with_fts(
    session: AsyncSession, initial_chat_setup: dict[str, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An oversized historical message remains discoverable as a bounded tool result."""
    chat_id = initial_chat_setup["chat_id"]
    archived = await append_message(chat_id, "main", MessageRole.USER, "ArchiveMarker " * 600, session)
    await append_message(chat_id, "main", MessageRole.MODEL, "OrphanMarker", session)
    hot_text = "Последний вопрос об ArchiveMarker"
    latest = await append_message(chat_id, "main", MessageRole.USER, hot_text, session)
    latest_id = latest.id
    budget = 4096
    assert _contents_size(map_messages_to_gemini([archived])) > budget
    await session.commit()
    monkeypatch.setattr(settings, "MAX_CONTEXT_BYTES", budget)
    requests = []

    async def generate(*, contents, tools):
        requests.append([item.model_copy(deep=True) for item in contents])
        if len(requests) == 1:
            return _make_tool_call_response([("search_history", {"query": "ArchiveMarker"})])
        return _make_text_response("Recovered")

    with patch("src.agent.client.genai.Client"):
        client = GeminiClient(api_key="test-key")
        client.generate_step = AsyncMock(side_effect=generate)
        answer = await run_turn(chat_id, "main", client, session)

    assert answer.parent_id == latest_id
    assert [item.role for item in requests[0]] == ["user"]
    assert [part.text for part in requests[0][0].parts] == [hot_text]
    tool_result = requests[1][-1].parts[0].function_response
    assert tool_result is not None
    assert "ArchiveMarker" in tool_result.response["result"]
    assert hot_text not in tool_result.response["result"]
    assert all(_contents_size(request) <= budget for request in requests)


def _contents_size(contents: list[types.Content]) -> int:
    return sum(len(item.model_dump_json().encode("utf-8")) for item in contents)


@pytest.mark.asyncio
async def test_turn_keeps_a_snapshot_for_context_and_memory_tools(file_sessions):
    """Правка и маска между вызовами Gemini не меняют уже начатый ход."""
    async with file_sessions() as writer:
        setup = await create_initial_chat(writer)
        chat_id, question_id = setup["chat_id"], setup["root_message_id"]
        await branch_from_here(chat_id, question_id, "side", writer)
        archive = await append_message(chat_id, "side", MessageRole.USER, "ArchiveMarker", writer)
        archive_id = archive.id
        await writer.commit()

    requests = []

    async def generate(*, contents, tools):
        requests.append([item.model_copy(deep=True) for item in contents])
        if len(requests) == 1:
            async with file_sessions.begin() as writer:
                await update_message_content(question_id, "Edited later", force=True, session=writer)
                await toggle_message_active(archive_id, writer)
            return _make_tool_call_response([("search_history", {"query": "ArchiveMarker"})])
        return _make_text_response("Reply")

    with patch("src.agent.client.genai.Client"):
        client = GeminiClient(api_key="test-key")
        client.generate_step = AsyncMock(side_effect=generate)
        async with file_sessions() as session:
            await run_turn(chat_id, "main", client, session, expected_question_id=question_id)

    assert all("Edited later" not in item.model_dump_json() for item in requests[1])
    result = requests[1][-1].parts[0].function_response
    assert "ArchiveMarker" in result.response["result"]


@pytest.mark.asyncio
@pytest.mark.parametrize("matches", [True, False])
async def test_run_turn_checks_client_head_before_writing_or_generating(
    session: AsyncSession, initial_chat_setup: dict[str, str], matches: bool,
) -> None:
    chat_id = initial_chat_setup["chat_id"]
    head_id = initial_chat_setup["root_message_id"]
    expected = head_id if matches else str(uuid.uuid4())
    with patch("src.agent.client.genai.Client"):
        client = GeminiClient(api_key="test-key")
        client.generate_step = AsyncMock(return_value=_make_text_response("Reply"))
        if matches:
            answer = await run_turn(chat_id, "main", client, session, expected_head_id=expected)
            assert answer.parent_id == head_id
            client.generate_step.assert_awaited_once()
        else:
            with pytest.raises(BranchConflictError):
                await run_turn(
                    chat_id, "main", client, session, user_prompt="Unwritten",
                    expected_head_id=expected,
                )
            client.generate_step.assert_not_awaited()
            ids = await session.scalars(select(Message.id).where(Message.chat_id == chat_id))
            assert ids.all() == [head_id]


@pytest.mark.asyncio
async def test_prompt_exact_utf8_budget_and_tool_batch_overflow(
    session: AsyncSession, initial_chat_setup: dict[str, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat_id = initial_chat_setup["chat_id"]
    prompt = "Кириллица 🧪"
    only_prompt = map_messages_to_gemini([Message(role=MessageRole.USER, content=prompt)])
    budget = _contents_size(only_prompt)
    monkeypatch.setattr(settings, "MAX_CONTEXT_BYTES", budget - 1)

    with patch("src.agent.client.genai.Client"):
        client = GeminiClient(api_key="test-key")
        client.generate_step = AsyncMock()
        with pytest.raises(PromptTooLargeError):
            await run_turn(chat_id, "main", client, session, user_prompt=prompt)
        client.generate_step.assert_not_awaited()
        assert not session.in_transaction()

        monkeypatch.setattr(settings, "MAX_CONTEXT_BYTES", budget)
        client.generate_step = AsyncMock(side_effect=[
            _make_tool_call_response([("search_history", {"query": "Root"})]),
            _make_text_response("Reply"),
        ])
        answer = await run_turn(
            chat_id, "main", client, session, user_prompt=prompt,
            expected_head_id=initial_chat_setup["root_message_id"],
        )

    question = await session.get(Message, answer.parent_id)
    assert question is not None and question.content == prompt
    assert question.parent_id == initial_chat_setup["root_message_id"]
    assert client.generate_step.await_count == 2
    assert client.generate_step.await_args.kwargs["tools"] is None
    for call in client.generate_step.await_args_list:
        assert call.kwargs["contents"] == only_prompt


@pytest.mark.asyncio
async def test_turn_call_limit_falls_back_without_running_excess_batch(
    session, initial_chat_setup, monkeypatch,
):
    monkeypatch.setattr("src.agent.loop.MAX_TOOL_CALLS", 1)
    with patch("src.agent.client.genai.Client"), patch(
        "src.agent.loop.search_history_fts", new_callable=AsyncMock,
    ) as search:
        client = GeminiClient(api_key="test-key")
        client.generate_step = AsyncMock(side_effect=[
            _make_tool_call_response([
                ("search_history", {"query": "one"}),
                ("search_history", {"query": "two"}),
            ]),
            _make_text_response("Fallback"),
        ])
        answer = await run_turn(initial_chat_setup["chat_id"], "main", client, session)
    assert answer.content == "Fallback"
    assert client.generate_step.await_args.kwargs["tools"] is None
    search.assert_not_awaited()


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
@pytest.mark.parametrize("search_args", [
    {"query": "FastAPI"},
    {"query": "", "date_from": "2026-05-01", "date_to": "2026-05-31"},
])
async def test_run_turn_with_tool_call_and_real_fts_search(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
    search_args: dict[str, str],
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
    archived = await append_message(
        chat_id=chat_id,
        branch_name="archive-branch",
        role=MessageRole.USER,
        content="Исторический факт: проект написан на FastAPI.",
        session=session,
    )
    archived.created_at = datetime(2026, 5, 16, 14, 44, tzinfo=timezone.utc)
    await session.commit()

    with patch("src.agent.client.genai.Client"):
        client = GeminiClient(api_key="test-key")
        client.generate_step = AsyncMock(
            side_effect=[
                _make_tool_call_response([("search_history", search_args)]),
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

    search_tool = client.generate_step.await_args_list[0].kwargs["tools"][0]
    declaration = types.FunctionDeclaration.from_callable_with_api_option(callable=search_tool)
    assert declaration.parameters is not None
    properties = declaration.parameters.properties or {}
    assert set(properties) == {"query", "limit", "date_from", "date_to"}
    assert properties["date_from"].type == properties["date_to"].type == types.Type.STRING
    assert declaration.parameters.required == ["query"]

    meta = json.loads(model_msg.metadata_json or "{}")
    trace = meta["execution_trace"]
    assert trace["iterations_used"] == 1
    assert len(trace["steps"]) == 1
    assert trace["steps"][0]["tool_called"] == "search_history"
    assert trace["steps"][0]["arguments"] == search_args
    assert f"[ID: {archived.id}] [2026-05-16 14:44]" in trace["steps"][0]["output_summary"]
    assert "FastAPI" in trace["steps"][0]["output_summary"]
    history = client.generate_step.await_args_list[1].kwargs["contents"]
    assert history[-1].parts[0].function_response.id == "call-0"


@pytest.mark.asyncio
async def test_run_turn_registers_and_invokes_memory_navigation_tools(
    session: AsyncSession, initial_chat_setup: dict[str, str],
) -> None:
    """Gemini can navigate the current chat without choosing a chat or branch ID."""
    chat_id = initial_chat_setup["chat_id"]
    anchor_id = initial_chat_setup["root_message_id"]
    with patch("src.agent.client.genai.Client"):
        client = GeminiClient(api_key="test-key")
        client.generate_step = AsyncMock(side_effect=[
            _make_tool_call_response([
                ("get_chat_overview", {}),
                ("read_context_around", {"message_id": anchor_id, "radius": 1}),
            ]),
            _make_text_response("Нашёл исходный вопрос."),
        ])
        result = await run_turn(chat_id, "main", client, session, user_prompt="Текущий вопрос")

    declarations = {
        tool.__name__: types.FunctionDeclaration.from_callable_with_api_option(callable=tool)
        for tool in client.generate_step.await_args_list[0].kwargs["tools"]
    }
    assert declarations.keys() == {"search_history", "get_summary", "get_chat_overview", "read_context_around"}
    overview = declarations["get_chat_overview"].parameters
    assert overview is None or not overview.properties
    context = declarations["read_context_around"].parameters
    assert context is not None
    assert set(context.properties or {}) == {"message_id", "radius"}
    assert context.required == ["message_id"]

    steps = json.loads(result.metadata_json or "{}")["execution_trace"]["steps"]
    assert "Активных сообщений: 2" in steps[0]["output_summary"]
    assert f"[ID: {anchor_id}]" in steps[1]["output_summary"]
    assert "Текущий вопрос" in steps[1]["output_summary"]


@pytest.mark.asyncio
async def test_run_turn_excludes_every_context_node_from_search(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    chat_id = initial_chat_setup["chat_id"]
    first = await append_message(chat_id, "main", MessageRole.MODEL, "Archive first", session)
    second = await append_message(chat_id, "main", MessageRole.MODEL, "Archive second", session)
    pending = await append_message(chat_id, "main", MessageRole.USER, "Pending question", session)
    expected_ids = [initial_chat_setup["root_message_id"], first.id, second.id, pending.id]
    await session.commit()

    with patch("src.agent.client.genai.Client"), patch(
        "src.agent.loop.search_history_fts", new_callable=AsyncMock,
        return_value="Ничего не найдено.",
    ) as search:
        client = GeminiClient(api_key="test-key")
        client.generate_step = AsyncMock(side_effect=[
            _make_tool_call_response([("search_history", {"query": "Archive"})]),
            _make_text_response("Ответ"),
        ])
        await run_turn(chat_id, "main", client, session)

    assert search.await_args.kwargs["exclude_message_ids"] == expected_ids


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
async def test_run_turn_does_not_commit_callers_open_transaction(
    file_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with file_sessions() as session:
        chat_id = (await create_initial_chat(session))["chat_id"]
        chat = await session.get(Chat, chat_id)
        assert chat is not None
        chat.title = "Pending title"
        await session.flush()

        with patch("src.agent.client.genai.Client"):
            client = GeminiClient(api_key="test-key")
            client.generate_step = AsyncMock()
            with pytest.raises(ValueError, match="без открытой транзакции"):
                await run_turn(chat_id, "main", client, session, user_prompt="Question")
            client.generate_step.assert_not_awaited()

        await session.rollback()

    async with file_sessions() as session:
        chat = await session.get(Chat, chat_id)
        assert chat is not None and chat.title == "Test Chat"


@pytest.mark.asyncio
async def test_run_turn_api_failure_preserves_user_message(
    file_sessions: async_sessionmaker[AsyncSession],
) -> None:
    with patch("src.agent.client.genai.Client"):
        client = GeminiClient(api_key="test-key")
        client.generate_step = AsyncMock(
            side_effect=[
                RuntimeError("503 Gemini Service Unavailable"),
                _make_text_response("Ответ после сбоя"),
            ]
        )

        async with file_sessions() as session:
            setup = await create_initial_chat(session)
            chat_id = setup["chat_id"]
            with pytest.raises(LLMGenerationError):
                await run_turn(
                    chat_id=chat_id,
                    branch_name="main",
                    client=client,
                    session=session,
                    user_prompt="Важная надиктованная мысль",
                )

        stmt = select(Branch).where(Branch.chat_id == chat_id, Branch.name == "main")
        async with file_sessions() as session:
            branch = (await session.execute(stmt)).scalar_one()
            head_msg = await session.get(Message, branch.head_message_id)
            assert head_msg is not None
            assert head_msg.role == MessageRole.USER
            assert head_msg.content == "Важная надиктованная мысль"
            assert head_msg.parent_id == setup["root_message_id"]
            user_message_id = head_msg.id

        async with file_sessions() as session:
            answer = await run_turn(chat_id, "main", client, session)
            answer_id = answer.id

        async with file_sessions() as session:
            branch = (await session.execute(stmt)).scalar_one()
            assert branch.head_message_id == answer_id
            assert (await session.get(Message, answer_id)).parent_id == user_message_id


@pytest.mark.asyncio
async def test_run_turn_rejects_head_changed_by_merge(
    file_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with file_sessions() as session:
        setup = await create_initial_chat(session)
        chat_id = setup["chat_id"]
        await branch_from_here(chat_id, setup["root_message_id"], "side", session)
        await append_message(chat_id, "side", MessageRole.USER, "Side delta", session)
        await session.commit()

    async def generate_after_merge(**kwargs):
        assert not session.in_transaction()
        async with file_sessions() as writer:
            await squash_merge_branch(
                chat_id, "side", summarizer_func=AsyncMock(return_value="Summary"), session=writer,
            )
            await writer.commit()
        return _make_text_response("Stale reply")

    with patch("src.agent.client.genai.Client"):
        client = GeminiClient(api_key="test-key")
        client.generate_step = AsyncMock(side_effect=generate_after_merge)
        async with file_sessions() as session:
            with pytest.raises(BranchConflictError):
                await run_turn(chat_id, "main", client, session, user_prompt="Question")

    async with file_sessions() as session:
        branches = {
            branch.name: branch
            for branch in await session.scalars(select(Branch).where(Branch.chat_id == chat_id))
        }
        merge = (await session.scalars(select(MergeEvent))).one()
        assert branches["main"].head_message_id == merge.id
        assert branches["side"].last_merged_message_id == branches["side"].head_message_id
        assert (await session.get(Message, merge.parent_id)).content == "Question"
        assert await session.scalar(select(Message.id).where(Message.content == "Stale reply")) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_branch", ["main", "side"])
async def test_merge_rejects_branch_changed_during_summary(
    file_sessions: async_sessionmaker[AsyncSession],
    changed_branch: str,
) -> None:
    async with file_sessions() as session:
        setup = await create_initial_chat(session)
        chat_id = setup["chat_id"]
        await branch_from_here(chat_id, setup["root_message_id"], "side", session)
        await append_message(chat_id, "side", MessageRole.USER, "Side delta", session)
        await session.commit()

    async def summarize_after_append(delta, previous_summary=None):
        async with file_sessions() as writer:
            await append_message(chat_id, changed_branch, MessageRole.USER, "Concurrent update", writer)
            await writer.commit()
        return "Stale summary"

    with pytest.raises(BranchConflictError):
        async with file_sessions.begin() as session:
            await squash_merge_branch(
                chat_id, "side", summarizer_func=summarize_after_append, session=session,
            )

    async with file_sessions() as session:
        branches = {
            branch.name: branch
            for branch in await session.scalars(select(Branch).where(Branch.chat_id == chat_id))
        }
        head = await session.get(Message, branches[changed_branch].head_message_id)
        assert head is not None and head.content == "Concurrent update"
        assert branches["side"].last_merged_message_id is None
        assert await session.scalar(select(MergeEvent.id)) is None


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
    await session.commit()

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
async def test_tool_failure_logs_type_without_database_payload(
    session: AsyncSession, initial_chat_setup: dict[str, str], caplog: pytest.LogCaptureFixture,
) -> None:
    details = OperationalError("SELECT local_marker", {"param": "private_marker"}, RuntimeError())
    with patch("src.agent.client.genai.Client"), patch(
        "src.agent.loop.search_history_fts", new=AsyncMock(side_effect=details),
    ):
        client = GeminiClient(api_key="test-key")
        client.generate_step = AsyncMock(side_effect=[
            _make_tool_call_response([("search_history", {"query": "example"})]),
            _make_text_response("Ответ после сбоя инструмента"),
        ])
        answer = await run_turn(initial_chat_setup["chat_id"], "main", client, session)

    followup = client.generate_step.await_args_list[1].kwargs["contents"][-1]
    output = followup.parts[0].function_response.response["result"]
    trace = json.loads(answer.metadata_json)["execution_trace"]["steps"][0]
    assert "Ошибка" in output
    assert trace["output_summary"] == output
    assert "gemini_tool_failed" in caplog.text
    assert "tool=search_history error_type=OperationalError" in caplog.text
    for marker in ("local_marker", "private_marker"):
        assert marker not in output
        assert marker not in caplog.text


@pytest.mark.asyncio
async def test_provider_failure_after_history_lookup_is_correlated_with_the_tool(
    session: AsyncSession, initial_chat_setup: dict[str, str], caplog: pytest.LogCaptureFixture,
) -> None:
    """A real local lookup completes before the mocked provider rejects the next round."""
    chat_id = initial_chat_setup["chat_id"]
    archived = Message(chat_id=chat_id, role=MessageRole.USER, content="PrivateArchiveMarker")
    session.add(archived)
    await session.commit()
    caplog.set_level("INFO", logger="uvicorn.error.exocortex")
    failure = ServerError(503, {"error": {"status": "UNAVAILABLE", "message": "private-provider-body"}})
    client = GeminiClient(api_key="private-key", model_name="test-model")
    client.generate_step = AsyncMock(side_effect=[
        _make_tool_call_response([("search_history", {"query": "PrivateArchiveMarker"})]),
        failure,
    ])
    with pytest.raises(LLMGenerationError, match="Сбой вызова Gemini") as caught:
        await run_turn(chat_id, "main", client, session)

    assert caught.value.__cause__ is failure
    followup = client.generate_step.await_args.kwargs["contents"][-1]
    assert "PrivateArchiveMarker" in followup.parts[0].function_response.response["result"]
    started_log, tool_log, failure_log = [
        record.getMessage() for record in caplog.records if record.name == "uvicorn.error.exocortex"
    ]
    assert "gemini_tool_started" in started_log
    assert "tool=search_history outcome=ok" in tool_log
    assert started_log.split()[1] == tool_log.split()[1] == failure_log.split()[1]
    assert "phase=after_tools model=test-model" in failure_log
    assert "kind=provider_http upstream_http=503 upstream_status=UNAVAILABLE" in failure_log
    assert "private-provider-body" in caplog.text
    assert all(marker not in caplog.text for marker in ("PrivateArchiveMarker", "private-key"))


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
    file_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Проверка защиты ветки от одновременного запуска двух генераций (BranchBusyError)."""
    async with file_sessions() as session:
        chat_id = (await create_initial_chat(session))["chat_id"]
    generation_started = asyncio.Event()
    allow_finish = asyncio.Event()

    async def slow_generate(*args: Any, **kwargs: Any) -> types.GenerateContentResponse:
        generation_started.set()
        await allow_finish.wait()
        return _make_text_response("Ответ первого запроса")

    with patch("src.agent.client.genai.Client"):
        client = GeminiClient(api_key="test-key")
        client.generate_step = AsyncMock(side_effect=slow_generate)

        async with file_sessions() as first, file_sessions() as second:
            task = asyncio.create_task(
                run_turn(chat_id, "main", client, first, user_prompt="Первый запрос")
            )
            try:
                await asyncio.wait_for(generation_started.wait(), timeout=5)
                with pytest.raises(BranchBusyError):
                    await run_turn(chat_id, "main", client, second, user_prompt="Второй запрос")
            finally:
                allow_finish.set()
                await asyncio.wait_for(task, timeout=5)
