from collections.abc import Callable
from typing import Any, Optional

from google import genai
from google.genai import types

from src.agent.prompts import (
    MENTOR_SYSTEM_INSTRUCTION,
    SUMMARIZER_SYSTEM_INSTRUCTION,
    SUMMARIZER_USER_PROMPT_TEMPLATE,
)
from src.storage.models import Message, MessageRole


def map_messages_to_gemini(messages: list[Message]) -> list[types.Content]:
    """Транслирует доменные узлы графа в типизированный список Content для Gemini API."""
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

        contents.append(
            types.Content(
                role=api_role,
                parts=[types.Part.from_text(text=text)],
            )
        )

    return contents


class GeminiClient:
    """Адаптер (Anti-Corruption Layer) для взаимодействия с Google GenAI SDK."""

    def __init__(
        self,
        api_key: str,
        model_name: str = "gemini-2.0-flash",
    ) -> None:
        self._model_name = model_name
        self._client = genai.Client(api_key=api_key)

    async def generate_step(
        self,
        contents: list[types.Content],
        tools: Optional[list[Callable[..., Any]]] = None,
        system_instruction: Optional[str] = MENTOR_SYSTEM_INSTRUCTION,
        temperature: float = 0.7,
        top_p: float = 0.95,
    ) -> types.GenerateContentResponse:
        """Выполняет одиночный асинхронный шаг инференса модели."""
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
            top_p=top_p,
            tools=tools,
        )
        return await self._client.aio.models.generate_content(
            model=self._model_name,
            contents=contents,
            config=config,
        )

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
        response = await self._client.aio.models.generate_content(
            model=self._model_name,
            contents=prompt,
            config=config,
        )
        return (response.text or "").strip()