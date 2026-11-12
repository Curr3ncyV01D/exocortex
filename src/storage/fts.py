import re
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession


class FTSQueryBuilder:
    """Инкапсулирует санитизацию и морфологическую нормализацию запросов для SQLite FTS5."""

    RESERVED_OPERATORS = frozenset({"AND", "OR", "NOT"})
    _SUFFIXES = re.compile(
        r"(ами|ями|ому|ему|ого|его|ыми|ей|ой|ий|ый|ая|ое|ее|ам|ям|ом|ем|ах|ях|ов|ев|ия|ие|ию|ья|ью|у|ю|а|я|е|и|ы|о)$",
        re.IGNORECASE,
    )

    @classmethod
    def stem_word(cls, word: str) -> str:
        """Отсекает регулярные падежные окончания для префиксного поиска по основе."""
        if len(word) <= 3:
            return word
        stemmed = cls._SUFFIXES.sub("", word)
        return stemmed if len(stemmed) >= 2 else word

    @classmethod
    def build(cls, raw_query: str) -> str:
        """Преобразует пользовательский запрос в безопасный поисковый терм FTS5."""
        tokens = re.findall(r"\w+", raw_query)
        if not tokens:
            return ""

        processed_parts: list[str] = []
        for token in tokens:
            upper_token = token.upper()
            if upper_token in cls.RESERVED_OPERATORS:
                processed_parts.append(f'"{upper_token}"')
            else:
                base = cls.stem_word(token)
                processed_parts.append(f"{base}*")

        return " ".join(processed_parts)


async def query_messages_fts(
    chat_id: str,
    raw_query: str,
    exclude_ids: set[str],
    session: AsyncSession,
    limit: int = 5,
) -> list[Row[Any]]:
    """Выполняет полнотекстовый поиск по индексу messages_fts с фильтрацией активного контекста."""
    sanitized_query = FTSQueryBuilder.build(raw_query)
    if not sanitized_query:
        return []

    fetch_limit = limit + len(exclude_ids)

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

    filtered_rows: list[Row[Any]] = []
    for row in rows:
        if row.id in exclude_ids:
            continue
        filtered_rows.append(row)
        if len(filtered_rows) >= limit:
            break

    return filtered_rows