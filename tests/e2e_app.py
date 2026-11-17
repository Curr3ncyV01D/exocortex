"""Изолированный FastAPI для браузерной приёмки."""

import os

from google.genai import types

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

from src.agent.client import GeminiClient
from src.api.deps import get_gemini_client
from src.main import app


class TestGemini(GeminiClient):
    def __init__(self) -> None:
        pass  # The fake provider never constructs an SDK client.

    async def generate_step(self, **kwargs):
        return types.GenerateContentResponse(candidates=[types.Candidate(
            finish_reason=types.FinishReason.STOP,
            content=types.Content(role="model", parts=[types.Part(text="Ответ тестовой модели")]),
        )])

    async def summarize_delta(self, *args, **kwargs):
        return "Выжимка тестовой ветки"


app.dependency_overrides[get_gemini_client] = TestGemini
