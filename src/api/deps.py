from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent.client import GeminiClient
from src.config import settings
from src.storage.database import get_db_session


async def get_session() -> AsyncIterator[AsyncSession]:
    async with aclosing(get_db_session()) as sessions:
        async for session in sessions:
            yield session


async def get_gemini_client() -> AsyncIterator[GeminiClient]:
    client = GeminiClient(api_key=settings.GEMINI_API_KEY)
    try:
        yield client
    finally:
        await client.aclose()


SessionDep = Annotated[AsyncSession, Depends(get_session)]
GeminiDep = Annotated[GeminiClient, Depends(get_gemini_client)]
