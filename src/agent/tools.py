import re
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.storage.models import MergeEvent


RESERVED_FTS_KEYWORDS = {"AND", "OR", "NOT"}
RUSSIAN_ENDINGS = re.compile(r"(ами|ями|ией|иям|ого|его|ому|ему|ыми|ими|а|е|и|о|у|ы|э|ю|я|ей|ой|ем|ом|им|ым)$", re.IGNORECASE)


def sanitize_fts_query(query: str) -> str:
    """Очищает строку, нормализует основы слов и формирует безопасный префиксный FTS-запрос."""
    tokens = re.findall(r"\w+", query)
    if not tokens:
        return ""

    safe_terms: list[str] = []
    for token in tokens:
        upper_token = token.upper()
        if upper_token in RESERVED_FTS_KEYWORDS:
            safe_terms.append(f'"{upper_token}"')
        else:
            # Отсекаем падежное окончание для русского слова длиннее 3 букв
            stem = token
            if len(token) > 3:
                stem = RUSSIAN_ENDINGS.sub("", token)
            safe_terms.append(f"{stem}*")

    return " ".join(safe_terms)


async def search_history_fts(
    chat_id: str,
    query: str,
    exclude_message_ids: Optional[list[str]] = None,
    session: Optional[AsyncSession] = None,
    limit: int = 5,
) -> str:
    """Выполняет детерминированный полнотекстовый поиск по истории диалога с усечением сниппетов."""
    if session is None:
        raise ValueError("Требуется активная асинхронная сессия БД.")

    sanitized_query = sanitize_fts_query(query)
    if not sanitized_query:
        return "Ничего не найдено."

    excluded_ids = set(exclude_message_ids or [])
    fetch_limit = limit + len(excluded_ids)

    stmt = text("""
        SELECT m.id, m.role, m.content, m.created_at
        FROM messages_fts f
        JOIN messages m ON f.rowid = m.rowid
        WHERE messages_fts MATCH :query
          AND m.chat_id = :chat_id
          AND m.is_active = 1
        ORDER BY rank
        LIMIT :fetch_limit
    """)

    result = await session.execute(
        stmt,
        {
            "query": sanitized_query,
            "chat_id": chat_id,
            "fetch_limit": fetch_limit,
        },
    )
    rows = result.fetchall()

    snippets: list[str] = []
    for row in rows:
        if row.id in excluded_ids:
            continue

        content = row.content
        if len(content) > 800:
            content = content[:800] + " [...]"

        if hasattr(row.created_at, "strftime"):
            created_str = row.created_at.strftime("%Y-%m-%d %H:%M")
        else:
            created_str = str(row.created_at)[:16]

        snippets.append(f"[{created_str}] {row.role}: {content}")

        if len(snippets) >= limit:
            break

    if not snippets:
        return "Ничего не найдено."

    return "\n---\n".join(snippets)


async def get_branch_summary(
    chat_id: str,
    branch_name: str,
    session: Optional[AsyncSession] = None,
) -> str:
    """Возвращает последний инкрементальный срез слияния побочной ветки."""
    if session is None:
        raise ValueError("Требуется активная асинхронная сессия БД.")

    stmt = (
        select(MergeEvent)
        .where(
            MergeEvent.chat_id == chat_id,
            MergeEvent.source_branch_name == branch_name,
        )
        .order_by(MergeEvent.created_at.desc())
        .limit(1)
    )

    event = (await session.execute(stmt)).scalar_one_or_none()
    if not event:
        return f"Сводка для ветки '{branch_name}' не найдена."

    return f"Сводка ветки '{branch_name}': {event.content}"