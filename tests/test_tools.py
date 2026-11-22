import re
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent import tools
from src.agent.tools import get_branch_summary, search_history_fts
from src.storage.crud.graph import set_message_active, update_message_content
from src.storage.models import Branch, BranchStatus, Chat, MergeEvent, Message, MessageRole


@pytest.mark.asyncio
async def test_read_context_around_clamps_radius_and_skips_masked_nodes(
    session: AsyncSession, initial_chat_setup: dict[str, str],
) -> None:
    """Navigate both directions through hidden nodes without returning their content."""
    chat_id = initial_chat_setup["chat_id"]
    chain: list[Message] = []
    for index in range(15):
        chain.append(Message(
            chat_id=chat_id,
            parent=chain[-1] if chain else None,
            content="x" * 900 if index == 7 else f"Реплика {index}",
            is_active=index not in (6, 8),
        ))
    session.add_all(chain)
    await session.commit()

    center = chain[7]
    short = await tools.get_history_context(chat_id, center.id, 0, session=session)
    assert re.findall(r"\[ID: ([^\]]+)\]", short) == [chain[i].id for i in (5, 7, 9)]
    assert "x" * 800 + " [...]" in short
    assert "x" * 801 not in short

    wide = await tools.get_history_context(chat_id, center.id, 100, session=session)
    assert re.findall(r"\[ID: ([^\]]+)\]", wide) == [
        message.id for message in chain[1:14] if message.is_active
    ]


@pytest.mark.asyncio
async def test_read_context_around_respects_branch_and_chat_boundaries(
    session: AsyncSession, initial_chat_setup: dict[str, str],
) -> None:
    """Use the current branch at a fork, and stop when its direction is ambiguous."""
    chat_id = initial_chat_setup["chat_id"]
    root = await session.get(Message, initial_chat_setup["root_message_id"])
    assert root is not None
    fork = Message(chat_id=chat_id, parent=root, content="Общий узел")
    hidden = Message(chat_id=chat_id, parent=fork, content="Скрытый узел", is_active=False)
    preferred = Message(chat_id=chat_id, parent=hidden, content="Выбранная ветка")
    after_head = Message(chat_id=chat_id, parent=preferred, content="За вершиной")
    other = Message(chat_id=chat_id, parent=fork, content="Другая ветка")
    foreign = Message(chat=Chat(title="Другой чат"), content="Чужая реплика")
    session.add_all([after_head, other, foreign])
    await session.commit()

    local = await tools.get_history_context(chat_id, fork.id, session=session, head_id=preferred.id)
    assert root.id in local and fork.id in local and preferred.id in local
    assert all(node.id not in local for node in (hidden, other, after_head, foreign))

    ambiguous = await tools.get_history_context(chat_id, fork.id, session=session, head_id=root.id)
    assert fork.id in ambiguous and "развилк" in ambiguous
    assert preferred.id not in ambiguous and other.id not in ambiguous

    for unavailable in (hidden.id, foreign.id, "unknown-message"):
        assert await tools.get_history_context(chat_id, unavailable, session=session) == "Активное сообщение не найдено."


@pytest.mark.asyncio
async def test_chat_overview_counts_active_history_and_checks_latest_summary(
    session: AsyncSession, initial_chat_setup: dict[str, str],
) -> None:
    """A hidden latest merge does not make an older summary available."""
    chat_id = initial_chat_setup["chat_id"]
    root = await session.get(Message, initial_chat_setup["root_message_id"])
    assert root is not None
    root.created_at = datetime(2026, 5, 1, tzinfo=timezone.utc)
    date = datetime(2026, 6, 1, tzinfo=timezone.utc)
    old_summary = MergeEvent(
        chat_id=chat_id, parent=root, content="Старая выжимка", created_at=date,
        source_branch_name="разбор/Первая ветка", forked_from_msg_id=root.id, compressed_messages_count=1,
    )
    new_summary = MergeEvent(
        chat_id=chat_id, parent=old_summary, content="Последняя выжимка", created_at=date, is_active=False,
        source_branch_name="разбор/Первая ветка", forked_from_msg_id=root.id, compressed_messages_count=1,
    )
    hidden = Message(chat_id=chat_id, content="Скрытая крайняя дата", is_active=False,
                     created_at=datetime(2026, 12, 1, tzinfo=timezone.utc))
    foreign_chat = Chat(title="Чужой чат")
    foreign_root = Message(
        id=str(uuid.uuid4()), chat=foreign_chat, content="Чужая история",
        created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    foreign_summary = MergeEvent(
        chat=foreign_chat, parent=foreign_root, content="Чужая выжимка",
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        source_branch_name="разбор/Первая ветка", forked_from_msg_id=foreign_root.id, compressed_messages_count=1,
    )
    session.add_all([
        new_summary, hidden,
        Branch(chat_id=chat_id, name="разбор/Первая ветка", status=BranchStatus.MERGED),
        Branch(chat_id=chat_id, name="архив", status=BranchStatus.ARCHIVED),
        foreign_summary,
        Branch(chat=foreign_chat, name="чужая ветка"),
        Branch(chat=foreign_chat, name="разбор/Первая ветка"),
    ])
    await session.commit()

    overview = await tools.get_chat_overview(chat_id, session=session)
    assert "Активных сообщений: 2" in overview
    assert "2026-05-01 00:00 — 2026-06-01 00:00" in overview
    assert "main (active)" in overview and "архив (archived)" in overview
    assert "разбор/Первая ветка (merged) — последняя сводка скрыта" in overview
    assert "чужая ветка" not in overview

    await set_message_active(new_summary.id, session, is_active=True)
    refreshed = await tools.get_chat_overview(chat_id, session=session)
    assert "Активных сообщений: 3" in refreshed
    assert "разбор/Первая ветка (merged) — сводка доступна" in refreshed


@pytest.mark.asyncio
async def test_chat_overview_handles_an_empty_chat(session: AsyncSession) -> None:
    chat = Chat(title="Пустой чат")
    session.add(Branch(chat=chat, name="main"))
    await session.commit()

    overview = await tools.get_chat_overview(chat.id, session=session)
    assert "Активных сообщений: 0" in overview
    assert "активных сообщений нет" in overview
    assert "main (active) — сводки нет" in overview


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

    await set_message_active(event2.id, session, is_active=False)
    summary = await get_branch_summary(chat_id, "feature/test", session)
    assert summary == "Актуальная сводка для ветки скрыта из контекста."
    await set_message_active(event2.id, session, is_active=True)
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

    await set_message_active(msg_id, session, is_active=False)

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
