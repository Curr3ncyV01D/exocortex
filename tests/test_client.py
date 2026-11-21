import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import pytest
from google.genai import types
from google.genai.errors import ClientError, ServerError

from src.agent.client import GeminiClient, map_messages_to_gemini
from src.agent.diagnostics import describe_failure, operation_scope, operation_id, operation_deadline
from src.config import settings
from src.storage.models import MergeEvent, Message, MessageRole


def test_map_messages_user_and_model() -> None:
    """Проверка маппинга стандартных ролей USER и MODEL в формат google-genai."""
    msg_user = Message(
        id=str(uuid.uuid4()),
        chat_id="chat-1",
        role=MessageRole.USER,
        content="Привет, ментор",
    )
    msg_model = Message(
        id=str(uuid.uuid4()),
        chat_id="chat-1",
        role=MessageRole.MODEL,
        content="Здравствуй. К делу.",
    )

    contents = map_messages_to_gemini([msg_user, msg_model])

    assert len(contents) == 2
    assert contents[0].role == "user"
    assert contents[0].parts[0].text == "Привет, ментор"
    assert contents[1].role == "model"
    assert contents[1].parts[0].text == "Здравствуй. К делу."


def test_map_messages_system_merge_formatting() -> None:
    """Проверка оборачивания узла SYSTEM_MERGE в системный префикс с ролью user."""
    merge_msg = MergeEvent(
        id=str(uuid.uuid4()),
        chat_id="chat-1",
        role=MessageRole.SYSTEM_MERGE,
        content="Итог: выбран FastAPI.",
        source_branch_name="explore-fastapi",
        forked_from_msg_id="root-1",
        compressed_messages_count=10,
    )

    contents = map_messages_to_gemini([merge_msg])

    assert len(contents) == 1
    assert contents[0].role == "user"
    expected_text = (
        "[СИСТЕМНЫЙ УЗЕЛ: Слияние ветки 'explore-fastapi']\n"
        "Итог: выбран FastAPI."
    )
    assert contents[0].parts[0].text == expected_text


def test_map_messages_empty_list() -> None:
    """Проверка обработки пустой истории сообщений."""
    assert map_messages_to_gemini([]) == []


def test_truncated_response_is_not_final() -> None:
    response = types.GenerateContentResponse(candidates=[types.Candidate(
        finish_reason=types.FinishReason.MAX_TOKENS,
        content=types.Content(role="model", parts=[types.Part.from_text(text="Partial")]),
    )])
    assert GeminiClient.extract_response_text(response) == ""


@pytest.mark.asyncio
async def test_default_model_used_for_generation_and_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "GEMINI_MODEL", "configured-model")
    with patch("src.agent.client.genai.Client") as sdk:
        generate = AsyncMock(return_value=types.GenerateContentResponse())
        sdk.return_value.aio.models.generate_content = generate
        client = GeminiClient(api_key="test-key")
        await client.generate_step([])
        await client.summarize_delta([])

    assert [call.kwargs["model"] for call in generate.await_args_list] == [
        "configured-model",
        "configured-model",
    ]


@pytest.mark.asyncio
async def test_gemini_client_generate_step_calls_sdk() -> None:
    """Проверка корректной передачи истории, промпта и тулов в асинхронный метод SDK."""
    mock_response = MagicMock()
    mock_response.text = "Ответ модели"

    with patch("src.agent.client.genai.Client") as mock_genai_cls:
        mock_sdk_instance = mock_genai_cls.return_value
        mock_sdk_instance.aio.models.generate_content = AsyncMock(
            return_value=mock_response
        )

        client = GeminiClient(api_key="test-key", model_name="explicit-model")
        dummy_contents = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text="Тестовый запрос")],
            )
        ]

        def dummy_tool(x: str) -> str:
            return x

        response = await client.generate_step(
            contents=dummy_contents,
            tools=[dummy_tool],
            system_instruction="Системная директива",
        )

        assert response.text == "Ответ модели"
        mock_sdk_instance.aio.models.generate_content.assert_awaited_once()

        call_kwargs = mock_sdk_instance.aio.models.generate_content.call_args.kwargs
        assert call_kwargs["model"] == "explicit-model"
        assert call_kwargs["contents"] == dummy_contents
        config: types.GenerateContentConfig = call_kwargs["config"]
        assert config.system_instruction == "Системная директива"
        assert config.tools == [dummy_tool]


@pytest.mark.asyncio
async def test_gemini_client_summarize_delta_formats_prompt() -> None:
    """Проверка формирования контекстного моста при инкрементальной суммаризации дельты."""
    mock_response = MagicMock()
    mock_response.text = "  Сжатый итог ветки.  "
    mock_response.candidates = []

    delta = [
        Message(
            id=str(uuid.uuid4()),
            chat_id="chat-1",
            role=MessageRole.USER,
            content="Решили отказаться от Django?",
        ),
        Message(
            id=str(uuid.uuid4()),
            chat_id="chat-1",
            role=MessageRole.MODEL,
            content="Да, берем FastAPI.",
        ),
    ]

    with patch("src.agent.client.genai.Client") as mock_genai_cls:
        mock_sdk_instance = mock_genai_cls.return_value
        mock_sdk_instance.aio.models.generate_content = AsyncMock(
            return_value=mock_response
        )

        client = GeminiClient(api_key="test-key")
        summary = await client.summarize_delta(
            delta_messages=delta,
            previous_summary="Ранее обсуждали выбор базы данных.",
        )

        assert summary == "Сжатый итог ветки."
        call_kwargs = mock_sdk_instance.aio.models.generate_content.call_args.kwargs
        prompt_sent: str = call_kwargs["contents"]
        assert "Ранее обсуждали выбор базы данных." in prompt_sent
        assert "Решили отказаться от Django?" in prompt_sent
        assert "Да, берем FastAPI." in prompt_sent


@pytest.mark.asyncio
async def test_gemini_client_summarize_delta_none_response() -> None:
    """Проверка защиты от падения при пустом или заблокированном ответе SDK (text is None)."""
    mock_response = MagicMock()
    mock_response.text = None

    delta = [
        Message(
            id=str(uuid.uuid4()),
            chat_id="chat-1",
            role=MessageRole.USER,
            content="Тестовая реплика",
        )
    ]

    with patch("src.agent.client.genai.Client") as mock_genai_cls:
        mock_sdk_instance = mock_genai_cls.return_value
        mock_sdk_instance.aio.models.generate_content = AsyncMock(
            return_value=mock_response
        )

        client = GeminiClient(api_key="test-key")
        summary = await client.summarize_delta(delta_messages=delta)

        assert summary == ""


def test_gemini_diagnostics_distinguish_proxy_network_and_timeout() -> None:
    for error, kind in (
        (httpx2.ProxyError("private proxy URL"), "proxy"),
        (httpx2.ConnectError("private request URL"), "transport"),
        (httpx2.ReadTimeout("private request URL"), "timeout"),
    ):
        description = describe_failure(error)
        assert f"kind={kind}" in description
        assert "private" in description


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    ServerError(503, {"error": {"status": "UNAVAILABLE", "message": "High demand"}}),
    ServerError(504, {"error": {"status": "DEADLINE_EXCEEDED", "message": "Deadline expired"}}),
    httpx2.ReadTimeout("Request timed out"),
    httpx2.ConnectError("Connection failed"),
    TimeoutError("Attempt deadline"),
])
async def test_generation_retries_transient_failures(failure: Exception) -> None:
    """Two transient failures recover on attempt three with bounded backoff."""
    result = types.GenerateContentResponse()
    with patch("src.agent.client.genai.Client") as sdk, patch(
        "src.agent.client.asyncio.sleep", new_callable=AsyncMock,
    ) as sleep, patch("src.agent.client.random.uniform", return_value=0):
        generate = sdk.return_value.aio.models.generate_content = AsyncMock(
            side_effect=[failure, failure, result],
        )
        client = GeminiClient(api_key="test-key")
        assert await client.generate_step([]) is result

    assert generate.await_count == 3
    assert [call.args[0] for call in sleep.await_args_list] == [1.5, 3.0]
    assert generate.await_args.kwargs["config"].http_options.retry_options.attempts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [400, 401, 403, 404, 429])
async def test_generation_does_not_retry_client_errors(code: int) -> None:
    failure = ClientError(code, {"error": {"message": "Prerequisite not met"}})
    with patch("src.agent.client.genai.Client") as sdk, patch(
        "src.agent.client.asyncio.sleep", new_callable=AsyncMock,
    ) as sleep:
        generate = sdk.return_value.aio.models.generate_content = AsyncMock(side_effect=failure)
        with pytest.raises(ClientError) as caught:
            await GeminiClient(api_key="test-key").generate_step([])
    assert caught.value is failure
    generate.assert_awaited_once()
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_generation_stops_after_three_failures() -> None:
    failure = ServerError(503, {"error": {"status": "UNAVAILABLE", "message": "High demand"}})
    with patch("src.agent.client.genai.Client") as sdk, patch(
        "src.agent.client.asyncio.sleep", new_callable=AsyncMock,
    ) as sleep:
        generate = sdk.return_value.aio.models.generate_content = AsyncMock(side_effect=failure)
        with pytest.raises(ServerError) as caught:
            await GeminiClient(api_key="test-key").generate_step([])
    assert caught.value is failure
    assert generate.await_count == 3 and sleep.await_count == 2


@pytest.mark.asyncio
async def test_generation_cancellation_interrupts_retries() -> None:
    with patch("src.agent.client.genai.Client") as sdk, patch(
        "src.agent.client.asyncio.sleep", new_callable=AsyncMock,
    ) as sleep:
        generate = sdk.return_value.aio.models.generate_content = AsyncMock(side_effect=asyncio.CancelledError)
        with pytest.raises(asyncio.CancelledError):
            await GeminiClient(api_key="test-key").generate_step([])
    generate.assert_awaited_once()
    sleep.assert_not_awaited()


def test_precondition_detail_retains_the_provider_explanation() -> None:
    error = ClientError(400, {"error": {
        "status": "FAILED_PRECONDITION",
        "message": "User location is not supported for the API use.\nCheck account.",
    }})
    diagnostic = describe_failure(error)
    assert "upstream_http=400 upstream_status=FAILED_PRECONDITION" in diagnostic
    assert "User location is not supported" in diagnostic
    assert "\n" not in diagnostic


@pytest.mark.asyncio
async def test_summary_retries_share_the_operation_identifier(caplog: pytest.LogCaptureFixture) -> None:
    failure = ServerError(503, {"error": {"status": "UNAVAILABLE", "message": "Busy"}})
    response = types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[types.Part(text="Summary")]),
        finish_reason=types.FinishReason.STOP,
    )])
    with patch("src.agent.client.genai.Client") as sdk, patch(
        "src.agent.client.asyncio.sleep", new_callable=AsyncMock,
    ):
        sdk.return_value.aio.models.generate_content = AsyncMock(side_effect=[failure, response])
        with operation_scope("summary-operation", 50):
            assert await GeminiClient(api_key="test-key").summarize_delta([]) == "Summary"
        assert sdk.return_value.aio.models.generate_content.await_count == 2
    assert "gemini_request_retry turn_id=summary-operation" in caplog.text
    assert operation_id.get() == "-" and operation_deadline.get() is None


@pytest.mark.asyncio
async def test_retry_stops_before_a_backoff_exceeds_the_remaining_budget() -> None:
    failure = ServerError(503, {"error": {"status": "UNAVAILABLE", "message": "Busy"}})
    with patch("src.agent.client.genai.Client") as sdk, patch(
        "src.agent.client.asyncio.sleep", new_callable=AsyncMock,
    ) as sleep:
        generate = sdk.return_value.aio.models.generate_content = AsyncMock(side_effect=failure)
        with operation_scope("short-budget", 0.5):
            with pytest.raises(TimeoutError, match="No time remains"):
                await GeminiClient(api_key="test-key").generate_step([])
    generate.assert_awaited_once()
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_budget_prevents_network_calls_and_restores_context() -> None:
    with patch("src.agent.client.genai.Client") as sdk:
        generate = sdk.return_value.aio.models.generate_content = AsyncMock()
        with operation_scope("expired-budget", -1):
            with pytest.raises(TimeoutError, match="exhausted"):
                await GeminiClient(api_key="test-key").generate_step([])
        generate.assert_not_awaited()
    assert operation_deadline.get() is None
