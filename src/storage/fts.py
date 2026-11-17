import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import islice

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.storage.models import Message, MessageRole

MAX_SEARCH_RESULTS = 10


@dataclass(frozen=True)
class SearchHit:
    id: str
    role: MessageRole
    content: str
    created_at: datetime


class InvalidSearchDateError(ValueError):
    """Дата поиска не соответствует формату или диапазон перевёрнут."""


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
    def build_terms(cls, raw_query: str) -> list[str]:
        """Преобразует слова запроса в безопасные термы FTS5."""
        tokens = re.findall(r"[^\W_]+", raw_query)
        processed_parts: list[str] = []
        for token in tokens:
            upper_token = token.upper()
            if upper_token in cls.RESERVED_OPERATORS:
                processed_parts.append(f'"{upper_token}"')
            else:
                base = cls.stem_word(token)
                processed_parts.append(f'"{base}"*')

        return processed_parts


def _parse_search_date(value: str | None, parameter: str) -> date | None:
    if value is None:
        return None
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise InvalidSearchDateError(f"{parameter} должна быть датой в формате YYYY-MM-DD (UTC).") from exc
    if parsed.isoformat() != value:
        raise InvalidSearchDateError(f"{parameter} должна быть датой в формате YYYY-MM-DD (UTC).")
    return parsed


async def query_messages_fts(
    chat_id: str,
    raw_query: str,
    exclude_ids: set[str],
    session: AsyncSession,
    limit: int = 5,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[SearchHit]:
    """Ищет активные реплики чата по термам FTS5 и/или диапазону дат UTC."""
    if limit <= 0:
        return []
    limit = min(limit, MAX_SEARCH_RESULTS)
    start = _parse_search_date(date_from, "date_from")
    end = _parse_search_date(date_to, "date_to")
    if start is not None and end is not None and start > end:
        raise InvalidSearchDateError("date_from должна быть не позднее date_to.")

    terms = FTSQueryBuilder.build_terms(raw_query)
    if not terms and (raw_query.strip() or (start is None and end is None)):
        return []

    filters = ["m.chat_id = :chat_id", "m.is_active = 1"]
    params: dict[str, str | int] = {"chat_id": chat_id, "fetch_limit": limit + len(exclude_ids)}
    if start is not None:
        filters.append("m.created_at >= :date_from")
        params["date_from"] = start.isoformat()
    if end is not None and end < date.max:
        filters.append("m.created_at < :date_before")
        params["date_before"] = (end + timedelta(days=1)).isoformat()

    source = "messages m"
    order = "m.created_at, m.rowid"
    if terms:
        source = "messages_fts JOIN messages m ON messages_fts.rowid = m.rowid"
        filters.append("messages_fts MATCH :query")
        order = "rank, m.id"

    # SQL fragments are fixed above. All search inputs are bound parameters.
    stmt = text(f"""
        SELECT m.id, m.role, m.content, m.created_at
        FROM {source}
        WHERE {" AND ".join(filters)}
        ORDER BY {order}
        LIMIT :fetch_limit
    """).columns(Message.id, Message.role, Message.content, Message.created_at)

    queries = [" ".join(terms)]
    if len(terms) > 1:
        queries.append(" OR ".join(terms))
    for query in queries:
        if query:
            params["query"] = query
        rows = await session.execute(stmt, params)
        hits = (
            SearchHit(row.id, row.role, row.content, row.created_at)
            for row in rows if row.id not in exclude_ids
        )
        filtered = list(islice(hits, limit))
        if filtered:
            return filtered
    return []
