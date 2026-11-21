import asyncio
import random
from time import monotonic
from uuid import uuid4
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional

from google import genai
from google.genai import types

from src.agent.prompts import (
    MENTOR_SYSTEM_INSTRUCTION,
    SUMMARIZER_SYSTEM_INSTRUCTION,
    SUMMARIZER_USER_PROMPT_TEMPLATE,
)
from src.agent.diagnostics import (
    describe_failure, is_transient_failure, logger,
    operation_id, operation_deadline, operation_scope,
)
from src.config import settings
from src.storage.models import Message, MessageRole

@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str | None


def map_messages_to_gemini(messages: list[Message]) -> list[types.Content]:
    """Преобразует узлы графа в историю Gemini с группировкой смежных ролей."""
    contents: list[types.Content] = []

    for msg in messages:
        if msg.role == MessageRole.USER:
            api_role = "user"
            text = msg.content
        elif msg.role == MessageRole.MODEL:
            api_role = "model"
            text = msg.content
        elif msg.role == MessageRole.SYSTEM_MERGE:
            api_role = "user"
            branch_name = getattr(msg, "source_branch_name", "unknown")
            text = (
                f"[СИСТЕМНЫЙ УЗЕЛ: Слияние ветки '{branch_name}']\n"
                f"{msg.content}"
            )
        else:
            api_role = "user"
            text = msg.content

        if contents and contents[-1].role == api_role:
            parts = contents[-1].parts
            if api_role == "model":
                parts[0] = types.Part.from_text(text=f"{parts[0].text}\n\n{text}")
            else:
                parts.append(types.Part.from_text(text=text))
        else:
            contents.append(
                types.Content(role=api_role, parts=[types.Part.from_text(text=text)])
            )

    return contents


class GeminiClient:
    """Адаптер (Anti-Corruption Layer) для взаимодействия с Google GenAI SDK."""

    def __init__(
        self,
        api_key: str,
        model_name: Optional[str] = None,
    ) -> None:
        self._model_name = settings.GEMINI_MODEL if model_name is None else model_name
        self._api_key = api_key
        self._sdk_client: genai.Client | None = None

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def _client(self) -> genai.Client:
        if self._sdk_client is None:
            self._sdk_client = genai.Client(api_key=self._api_key)
        return self._sdk_client

    async def aclose(self) -> None:
        if self._sdk_client is not None:
            try:
                await self._sdk_client.aio.aclose()
            finally:
                self._sdk_client.close()
                self._sdk_client = None

    async def generate_step(
        self,
        contents: list[types.Content],
        tools: Optional[list[Callable[..., Any]]] = None,
        system_instruction: Optional[str] = MENTOR_SYSTEM_INSTRUCTION,
        temperature: float = 0.7,
        top_p: float = 0.95,
    ) -> types.GenerateContentResponse:
        """Выполняет одиночный асинхронный шаг инференса с отключенным автовызовом тулов."""
        afc_config = (
            types.AutomaticFunctionCallingConfig(disable=True)
            if tools
            else None
        )
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
            top_p=top_p,
            tools=tools,
            automatic_function_calling=afc_config,
        )
        return await self._generate(contents, config)

    async def _generate(
        self, contents: list[types.Content] | str, config: types.GenerateContentConfig,
    ) -> types.GenerateContentResponse:
        if operation_deadline.get() is None:
            with operation_scope(uuid4().hex, settings.AGENT_OPERATION_TIMEOUT_SECONDS):
                return await self._generate(contents, config)
        deadline = operation_deadline.get()
        assert deadline is not None
        for attempt in range(1, 4):
            remaining = deadline - monotonic()
            if remaining <= 0:
                logger.warning("gemini_budget_exhausted turn_id=%s", operation_id.get())
                raise TimeoutError("Gemini operation time budget exhausted.")
            timeout = min(settings.GEMINI_REQUEST_TIMEOUT_SECONDS, remaining)
            config.http_options = types.HttpOptions(
                timeout=max(1, int(timeout * 1000)),
                retry_options=types.HttpRetryOptions(attempts=1),
            )
            try:
                async with asyncio.timeout(timeout):
                    return await self._client.aio.models.generate_content(
                        model=self._model_name,
                        contents=contents,
                        config=config,
                    )
            except Exception as error:
                if attempt == 3 or not is_transient_failure(error):
                    raise
                delay = 1.5 * 2 ** (attempt - 1) + random.uniform(0, 0.5)
                remaining = deadline - monotonic()
                if remaining <= delay:
                    logger.warning("gemini_budget_exhausted turn_id=%s %s", operation_id.get(), describe_failure(error))
                    raise TimeoutError("No time remains for another Gemini attempt.") from error
                logger.warning(
                    "gemini_request_retry turn_id=%s model=%s attempt=%d next_attempt=%d delay_seconds=%.2f remaining_seconds=%.2f %s",
                    operation_id.get(), self._model_name, attempt, attempt + 1, delay, remaining, describe_failure(error),
                )
                await asyncio.sleep(delay)
        raise AssertionError("Unreachable retry state.")

    @staticmethod
    def extract_function_calls(
        response: types.GenerateContentResponse,
    ) -> list[ToolCall]:
        """Извлекает запросы вызова инструментов из ответа SDK в нейтральный формат."""
        if not response.function_calls:
            return []
        return [
            ToolCall(call.name, dict(call.args or {}), call.id)
            for call in response.function_calls
            if call.name
        ]

    @staticmethod
    def extract_response_text(
        response: types.GenerateContentResponse,
    ) -> str:
        """Безопасно извлекает текстовый ответ модели с защитой от блокировок."""
        if response.candidates:
            reason = response.candidates[0].finish_reason
            if reason not in (None, types.FinishReason.STOP):
                return ""
        try:
            text = response.text
            return (text or "").strip()
        except ValueError:
            return ""

    @staticmethod
    def append_tool_interaction(
        contents: list[types.Content],
        model_response: types.GenerateContentResponse,
        tool_results: list[tuple[ToolCall, str]],
    ) -> None:
        """Добавляет шаг вызова инструментов модели и их отклики в оперативный буфер."""
        if model_response.candidates and model_response.candidates[0].content:
            contents.append(model_response.candidates[0].content)

        response_parts = [
            types.Part(
                function_response=types.FunctionResponse(
                    id=call.id, name=call.name, response={"result": result},
                ),
            )
            for call, result in tool_results
        ]
        contents.append(types.Content(role="user", parts=response_parts))

    @staticmethod
    def append_user_text(
        contents: list[types.Content],
        text: str,
    ) -> None:
        """Добавляет текстовую директиву в буфер с группировкой при смежной роли user."""
        part = types.Part.from_text(text=text)
        if contents and contents[-1].role == "user":
            contents[-1].parts.append(part)
        else:
            contents.append(types.Content(role="user", parts=[part]))

    async def summarize_delta(
        self,
        delta_messages: list[Message],
        previous_summary: Optional[str] = None,
    ) -> str:
        """Сжимает массив реплик ветки в аналитическую выжимку для узла слияния."""
        delta_lines = [
            f"[{msg.role.value.upper()}]: {msg.content}"
            for msg in delta_messages
        ]
        delta_text = "\n\n".join(delta_lines)
        prev_ctx = (
            previous_summary
            if previous_summary
            else "Отсутствует (первичное слияние)."
        )

        prompt = SUMMARIZER_USER_PROMPT_TEMPLATE.format(
            previous_summary=prev_ctx,
            delta_text=delta_text,
        )

        config = types.GenerateContentConfig(
            system_instruction=SUMMARIZER_SYSTEM_INSTRUCTION,
            temperature=0.3,
        )
        response = await self._generate(prompt, config)
        return self.extract_response_text(response)
