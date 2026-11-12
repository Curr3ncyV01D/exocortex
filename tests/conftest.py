import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from src.storage.database import Base, init_db
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