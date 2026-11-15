import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from google.genai import types
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from src.agent.client import GeminiClient
from src.schemas import BranchResponse, ChatResponse, MessageResponse
from src.storage.database import Base, configure_sqlite_pragmas, init_db
from src.storage.models import Branch, Chat, Message, MessageRole


@pytest_asyncio.fixture
async def async_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Инициализация изолированного движка SQLite в оперативной памяти со статическим пулом и FTS5."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        cursor.close()

    await init_db(engine)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture
async def file_sessions(
    tmp_path: Path,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    url = URL.create("sqlite+aiosqlite", database=str(tmp_path / "review.db"))
    engine = create_async_engine(url)
    event.listen(engine.sync_engine, "connect", configure_sqlite_pragmas)
    try:
        await init_db(engine)
        yield async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session(
    async_engine: AsyncEngine,
) -> AsyncGenerator[AsyncSession, None]:
    """Предоставляет транзакционную асинхронную сессию для каждого теста."""
    session_factory = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def initial_chat_setup(
    session: AsyncSession,
) -> dict[str, str]:
    """Создает базовый чат с корневым сообщением и веткой main с гарантированным порядком flush."""
    return await create_initial_chat(session)


async def create_initial_chat(session: AsyncSession) -> dict[str, str]:
    chat_id = str(uuid.uuid4())
    root_msg_id = str(uuid.uuid4())

    chat = Chat(id=chat_id, title="Test Chat")
    session.add(chat)

    root_message = Message(
        id=root_msg_id,
        chat_id=chat_id,
        parent_id=None,
        role=MessageRole.USER,
        content="Root prompt",
        is_active=True,
    )
    session.add(root_message)
    # Фиксируем родительские сущности до вставки ветки
    await session.flush()

    main_branch = Branch(
        id=str(uuid.uuid4()),
        chat_id=chat_id,
        name="main",
        head_message_id=root_msg_id,
        forked_from_id=None,
    )
    session.add(main_branch)
    await session.commit()

    return {
        "chat_id": chat_id,
        "root_message_id": root_msg_id,
        "branch_name": "main",
    }
@pytest.fixture
def api_gemini():
    with patch("src.agent.client.genai.Client"):
        client = GeminiClient(api_key="test-key")
        client.generate_step = AsyncMock(return_value=types.GenerateContentResponse(
            candidates=[types.Candidate(
                finish_reason=types.FinishReason.STOP,
                content=types.Content(role="model", parts=[types.Part(text="Test reply")]),
            )],
        ))
        client.summarize_delta = AsyncMock(return_value="Test summary")
        yield client


@pytest_asyncio.fixture
async def api_client(file_sessions, api_gemini) -> AsyncIterator[AsyncClient]:
    from src.api.deps import get_gemini_client, get_session
    from src.main import app

    async def session_override():
        async with file_sessions() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_gemini_client] = lambda: api_gemini
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


async def create_api_chat(client: AsyncClient) -> tuple[ChatResponse, BranchResponse]:
    response = await client.post("/api/v1/chats", json={"title": "Contract chat"})
    assert response.status_code == 201, response.text
    chat = ChatResponse.model_validate(response.json())
    response = await client.get(f"/api/v1/chats/{chat.id}/branches")
    assert response.status_code == 200, response.text
    (main,) = [BranchResponse.model_validate(row) for row in response.json()]
    assert main.chat_id == chat.id and main.name == "main"
    assert main.head_message_id is None and main.last_merged_message_id is None
    assert main.status.value == "active"
    return chat, main


async def append_api_message(client: AsyncClient, branch_id: str, content: str) -> MessageResponse:
    response = await client.post(f"/api/v1/branches/{branch_id}/messages", json={"content": content})
    assert response.status_code == 201, response.text
    return MessageResponse.model_validate(response.json())
