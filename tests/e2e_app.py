"""Изолированный FastAPI для браузерной приёмки."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from google.genai import types

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

from src.agent.client import GeminiClient
from src.api.deps import get_gemini_client
from src.main import app
from src.storage.database import engine
from src.storage.migrations import upgrade_schema

app_lifespan = app.router.lifespan_context


@asynccontextmanager
async def test_lifespan(test_app: FastAPI) -> AsyncIterator[None]:
    await upgrade_schema(engine)
    async with app_lifespan(test_app):
        yield


app.router.lifespan_context = test_lifespan


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
