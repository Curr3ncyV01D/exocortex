import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent.tools import get_branch_summary, search_history_fts
from src.storage.crud.graph import toggle_message_active, update_message_content
from src.storage.models import Branch, Chat, MergeEvent, Message, MessageRole


@pytest.mark.asyncio
async def test_history_search_uses_or_only_after_filtered_and_is_empty(
    session: AsyncSession, initial_chat_setup: dict[str, str],
) -> None:
    """Strict matches take precedence over partial matches outside the hot context."""
    chat_id = initial_chat_setup["chat_id"]
    exact = Message(chat_id=chat_id, content="alpha beta gamma")
    stronger = Message(chat_id=chat_id, content="alpha beta filler")
    weaker = Message(chat_id=chat_id, content="alpha filler filler")
    masked = Message(chat_id=chat_id, content="alpha beta gamma masked", is_active=False)
    other_chat = Chat(title="Other chat")
    foreign = Message(chat=other_chat, content="alpha beta gamma foreign")
    session.add_all([exact, stronger, weaker, masked, foreign])
    await session.commit()

    strict = await search_history_fts(chat_id, "alpha beta gamma", session=session)
    assert strict.startswith(f"[ID: {exact.id}]")
    assert stronger.id not in strict and weaker.id not in strict

    expanded = await search_history_fts(chat_id, "alpha beta gamma", [exact.id], session=session)
    assert expanded.startswith(f"[ID: {stronger.id}]")
    assert f"[ID: {weaker.id}]" in expanded
    assert all(message.id not in expanded for message in (exact, masked, foreign))


@pytest.mark.asyncio
async def test_history_search_dates_apply_to_both_query_modes(
    session: AsyncSession, initial_chat_setup: dict[str, str],
) -> None:
    """Include the full final day and apply the same filters to date-only and FTS results."""
    chat_id = initial_chat_setup["chat_id"]
    start = datetime(2026, 5, 1, tzinfo=timezone.utc)
    end = datetime(2026, 5, 1, 23, 59, 59, 999999, tzinfo=timezone.utc)
    next_day = datetime(2026, 5, 2, tzinfo=timezone.utc)
    first = Message(chat_id=chat_id, content="alpha first", created_at=start)
    last = Message(chat_id=chat_id, content="beta last", created_at=end)
    later = Message(chat_id=chat_id, content="alpha beta later", created_at=next_day)
    hot = Message(chat_id=chat_id, content="alpha beta hot", created_at=start)
    masked = Message(chat_id=chat_id, content="alpha beta masked", created_at=start, is_active=False)
    foreign = Message(chat=Chat(title="Other chat"), content="alpha beta foreign", created_at=start)
    session.add_all([last, first, later, hot, masked, foreign])
    await session.commit()

    result = await search_history_fts(
        chat_id, "", [hot.id], session=session, date_from="2026-05-01", date_to="2026-05-01",
    )
    assert result.split("\n---\n") == [
        f"[ID: {first.id}] [2026-05-01 00:00] user: alpha first",
        f"[ID: {last.id}] [2026-05-01 23:59] user: beta last",
    ]

    fts = await search_history_fts(
        chat_id, "alpha beta", [hot.id], session=session, date_to="2026-05-01",
    )
    assert first.id in fts and last.id in fts
    assert all(message.id not in fts for message in (later, hot, masked, foreign))

    since = await search_history_fts(chat_id, "", session=session, date_from="2026-05-02", limit=1)
    assert since.startswith(f"[ID: {later.id}]")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("date_from", "date_to", "expected"),
    [
        ("20260501", None, "YYYY-MM-DD"),
        (None, "2026-02-30", "YYYY-MM-DD"),
        ("2026-05-02", "2026-05-01", "date_from"),
    ],
)
async def test_history_search_reports_invalid_dates(
    session: AsyncSession, initial_chat_setup: dict[str, str],
    date_from: str | None, date_to: str | None, expected: str,
) -> None:
    result = await search_history_fts(
        initial_chat_setup["chat_id"], "", session=session, date_from=date_from, date_to=date_to,
    )
    assert result.startswith("Ошибка")
    assert expected in result


@pytest.mark.asyncio
async def test_search_zero_limit_with_exclusions(session, initial_chat_setup):
    assert await search_history_fts(
        initial_chat_setup["chat_id"], "Root", exclude_message_ids=["other"],
        session=session, limit=0,
    ) == "Ничего не найдено."

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
        "FastAPI_ORM",
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
        if query in {"FastAPI-ORM", "C++", "FastAPI_ORM"}:
            assert "FastAPI-ORM и C++" in result
        else:
            assert result == "Ничего не найдено."


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
        created_at=event1.created_at,
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

    await toggle_message_active(event2.id, session)
    summary = await get_branch_summary(chat_id, "feature/test", session)
    assert summary == "Актуальная сводка для ветки скрыта из контекста."
    await toggle_message_active(event2.id, session)
    assert "Срез 2" in await get_branch_summary(chat_id, "feature/test", session)


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


@pytest.mark.asyncio
async def test_search_history_fts_cross_chat_isolation(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка строгой изоляции поиска FTS5 между разными чатами."""
    chat_a_id = initial_chat_setup["chat_id"]
    root_a_id = initial_chat_setup["root_message_id"]

    session.add(
        Message(
            id=str(uuid.uuid4()),
            chat_id=chat_a_id,
            parent_id=root_a_id,
            role=MessageRole.USER,
            content="СверхсекретныйКлючДоступа только для чата А",
            is_active=True,
        )
    )

    chat_b_id = str(uuid.uuid4())
    session.add(Chat(id=chat_b_id, title="Chat B"))
    await session.commit()

    result = await search_history_fts(
        chat_id=chat_b_id,
        query="СверхсекретныйКлючДоступа",
        exclude_message_ids=[],
        session=session,
    )

    assert "СверхсекретныйКлючДоступа" not in result
    assert "Ничего не найдено" in result


@pytest.mark.asyncio
async def test_search_history_fts_sync_after_edit_and_toggle(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка обновления триггера FTS5 при редактировании и скрытия при маскировании."""
    chat_id = initial_chat_setup["chat_id"]
    root_id = initial_chat_setup["root_message_id"]
    msg_id = str(uuid.uuid4())

    session.add(
        Message(
            id=msg_id,
            chat_id=chat_id,
            parent_id=root_id,
            role=MessageRole.USER,
            content="Выбрали технологию Django",
            is_active=True,
        )
    )
    await session.commit()

    await update_message_content(
        message_id=msg_id,
        new_content="Перешли на технологию FastAPI",
        force=True,
        session=session,
    )

    res_old = await search_history_fts(
        chat_id=chat_id,
        query="Django",
        exclude_message_ids=[],
        session=session,
    )
    assert "Ничего не найдено" in res_old

    res_new = await search_history_fts(
        chat_id=chat_id,
        query="FastAPI",
        exclude_message_ids=[],
        session=session,
    )
    assert "FastAPI" in res_new

    await toggle_message_active(msg_id, session)

    res_disabled = await search_history_fts(
        chat_id=chat_id,
        query="FastAPI",
        exclude_message_ids=[],
        session=session,
    )
    assert "Ничего не найдено" in res_disabled


@pytest.mark.asyncio
async def test_search_history_fts_empty_or_punctuation_query(
    session: AsyncSession,
    initial_chat_setup: dict[str, str],
) -> None:
    """Проверка безопасной обработки пустых запросов и чистой пунктуации."""
    chat_id = initial_chat_setup["chat_id"]

    for empty_q in ["", "   ", "!!! ??? ---"]:
        result = await search_history_fts(
            chat_id=chat_id,
            query=empty_q,
            exclude_message_ids=[],
            session=session,
        )
        assert result == "Ничего не найдено."
