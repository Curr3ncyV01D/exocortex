from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.storage.crud.chats import get_chat_memory_overview, read_context_around
from src.storage.crud.graph import get_latest_merge_event
from src.storage.fts import InvalidSearchDateError, query_messages_fts
from src.storage.models import MessageRole


def _format_snippet(message_id: str, created_at: datetime, role: MessageRole, content: str) -> str:
    if len(content) > 800:
        content = content[:800] + " [...]"
    return f"[ID: {message_id}] [{created_at:%Y-%m-%d %H:%M}] {role.value}: {content}"


async def search_history_fts(
    chat_id: str,
    query: str,
    exclude_message_ids: Optional[list[str]] = None,
    *,
    session: AsyncSession,
    limit: int = 5,
    date_from: str | None = None,
    date_to: str | None = None,
) -> str:
    """Форматирует результаты поиска истории по тексту и датам."""
    excluded_ids = set(exclude_message_ids or [])
    try:
        rows = await query_messages_fts(
            chat_id=chat_id,
            raw_query=query,
            exclude_ids=excluded_ids,
            session=session,
            limit=limit,
            date_from=date_from,
            date_to=date_to,
        )
    except InvalidSearchDateError as error:
        return f"Ошибка поиска: {error}"

    if not rows:
        return "Ничего не найдено."

    return "\n---\n".join(_format_snippet(row.id, row.created_at, row.role, row.content) for row in rows)


async def get_history_context(
    chat_id: str, message_id: str, radius: int = 3, *, session: AsyncSession, head_id: str | None = None,
) -> str:
    """Format an active message and its graph neighbors."""
    neighborhood = await read_context_around(chat_id, message_id, radius, session, head_id=head_id)
    if neighborhood is None:
        return "Активное сообщение не найдено."
    snippets = [
        _format_snippet(message.id, message.created_at, message.role, message.content)
        for message in neighborhood.messages
    ]
    if neighborhood.stopped_at_fork:
        snippets.append("Дальше в истории развилка на несколько веток.")
    return "\n---\n".join(snippets)


async def get_chat_overview(chat_id: str, *, session: AsyncSession) -> str:
    """Format chat dates, volume and available branch summaries."""
    overview = await get_chat_memory_overview(chat_id, session)
    period = "активных сообщений нет"
    if overview.earliest is not None and overview.latest is not None:
        period = f"{overview.earliest:%Y-%m-%d %H:%M} — {overview.latest:%Y-%m-%d %H:%M}"
    lines = [f"Активных сообщений: {overview.active_count}", f"Период (UTC): {period}", "Ветки:"]
    summary_status = {
        None: "сводки нет",
        True: "сводка доступна",
        False: "последняя сводка скрыта",
    }
    lines.extend(
        f"- {branch.name} ({branch.status.value}) — {summary_status[branch.latest_summary_active]}"
        for branch in overview.branches
    )
    return "\n".join(lines)


async def get_branch_summary(
    chat_id: str,
    branch_name: str,
    session: AsyncSession,
) -> str:
    """Фасад извлечения последнего инкрементального среза слияния ветки."""
    event = await get_latest_merge_event(
        chat_id=chat_id,
        branch_name=branch_name,
        session=session,
    )
    if not event:
        return f"Сводка для ветки '{branch_name}' не найдена."
    if not event.is_active:
        return "Актуальная сводка для ветки скрыта из контекста."

    return f"Сводка ветки '{branch_name}': {event.content}"
