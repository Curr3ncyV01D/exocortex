import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent.tools import get_branch_summary, search_history_fts
from src.storage.models import Branch, MergeEvent, Message, MessageRole


@pytest.mark.asyncio
async def test_search_history_fts_morphology(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка нахождения реплик с видоизмененными падежными окончаниями слов."""
    chat_id = initial_chat_setup["chat_id"]
    root_id = initial_chat_setup["root_message_id"]

    msg = Message(
        id=str(uuid.uuid4()),
        chat_id=chat_id,
        parent_id=root_id,
        role=MessageRole.USER,
        content="Вчера я долго размышлял об учебе и сложных задачах",
        is_active=True,
    )
    session.add(msg)
    await session.commit()

    # Ищем начальную форму "учеба", в базе лежит "учебе"
    result = await search_history_fts(
        chat_id=chat_id,
        query="учеба",
        exclude_message_ids=[],
        session=session,
    )

    assert "учебе" in result


@pytest.mark.asyncio
async def test_search_history_fts_special_characters_safety(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка устойчивости санитизации запроса к спецсимволам синтаксиса FTS5."""
    chat_id = initial_chat_setup["chat_id"]
    root_id = initial_chat_setup["root_message_id"]

    msg = Message(
        id=str(uuid.uuid4()),
        chat_id=chat_id,
        parent_id=root_id,
        role=MessageRole.USER,
        content="Мы используем FastAPI-ORM и C++ в микросервисах",
        is_active=True,
    )
    session.add(msg)
    await session.commit()

    dangerous_queries = [
        "FastAPI-ORM",
        "C++",
        'NOT AND OR " " *',
        'query with "unclosed quotes',
    ]

    for query in dangerous_queries:
        result = await search_history_fts(
            chat_id=chat_id,
            query=query,
            exclude_message_ids=[],
            session=session,
        )
        assert isinstance(result, str)


@pytest.mark.asyncio
async def test_search_history_fts_excludes_active_context(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка исключения сообщений активной ветки из результатов поиска."""
    chat_id = initial_chat_setup["chat_id"]
    root_id = initial_chat_setup["root_message_id"]

    active_msg_id = str(uuid.uuid4())
    msg = Message(
        id=active_msg_id,
        chat_id=chat_id,
        parent_id=root_id,
        role=MessageRole.USER,
        content="Изолированный секретный токен безопасности",
        is_active=True,
    )
    session.add(msg)
    await session.commit()

    result = await search_history_fts(
        chat_id=chat_id,
        query="токен",
        exclude_message_ids=[active_msg_id],
        session=session,
    )

    assert "секретный токен" not in result
    assert "Ничего не найдено" in result


@pytest.mark.asyncio
async def test_search_history_fts_snippet_truncation(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка жесткого ограничения длины сниппета для защиты контекстного окна."""
    chat_id = initial_chat_setup["chat_id"]
    root_id = initial_chat_setup["root_message_id"]

    huge_content = "КлючевоеСлово " + ("ДлинныйТекст " * 300)
    msg = Message(
        id=str(uuid.uuid4()),
        chat_id=chat_id,
        parent_id=root_id,
        role=MessageRole.USER,
        content=huge_content,
        is_active=True,
    )
    session.add(msg)
    await session.commit()

    result = await search_history_fts(
        chat_id=chat_id,
        query="КлючевоеСлово",
        exclude_message_ids=[],
        session=session,
    )

    assert len(result) < 1200
    assert "[...]" in result


@pytest.mark.asyncio
async def test_get_branch_summary_returns_latest(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка возврата самого последнего инкрементального среза слияния ветки."""
    chat_id = initial_chat_setup["chat_id"]
    root_id = initial_chat_setup["root_message_id"]

    event1 = MergeEvent(
        id=str(uuid.uuid4()),
        chat_id=chat_id,
        parent_id=root_id,
        role=MessageRole.SYSTEM_MERGE,
        content="Срез 1: Начальные выводы",
        source_branch_name="feature/test",
        forked_from_msg_id=root_id,
        compressed_messages_count=3,
    )
    session.add(event1)
    await session.flush()

    event2 = MergeEvent(
        id=str(uuid.uuid4()),
        chat_id=chat_id,
        parent_id=event1.id,
        role=MessageRole.SYSTEM_MERGE,
        content="Срез 2: Финальные выводы архитектуры",
        source_branch_name="feature/test",
        forked_from_msg_id=event1.id,
        compressed_messages_count=5,
    )
    session.add(event2)
    await session.commit()

    summary = await get_branch_summary(
        chat_id=chat_id,
        branch_name="feature/test",
        session=session,
    )

    assert "Срез 2: Финальные выводы архитектуры" in summary
    assert "Срез 1" not in summary


@pytest.mark.asyncio
async def test_get_branch_summary_not_found(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка корректной обработки запроса несуществующего саммари ветки."""
    chat_id = initial_chat_setup["chat_id"]

    summary = await get_branch_summary(
        chat_id=chat_id,
        branch_name="non_existent_branch",
        session=session,
    )

    assert "Сводка для ветки 'non_existent_branch' не найдена" in summary