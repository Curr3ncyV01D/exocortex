from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.storage.crud.chats import get_latest_merge_event
from src.storage.fts import query_messages_fts


async def search_history_fts(
    chat_id: str,
    query: str,
    exclude_message_ids: Optional[list[str]] = None,
    session: Optional[AsyncSession] = None,
    limit: int = 5,
) -> str:
    """Фасад полнотекстового поиска по истории диалога с усечением сниппетов."""
    if session is None:
        raise ValueError("Требуется активная асинхронная сессия БД.")

    excluded_ids = set(exclude_message_ids or [])
    rows = await query_messages_fts(
        chat_id=chat_id,
        raw_query=query,
        exclude_ids=excluded_ids,
        session=session,
        limit=limit,
    )

    if not rows:
        return "Ничего не найдено."

    snippets: list[str] = []
    for row in rows:
        content = row.content
        if len(content) > 800:
            content = content[:800] + " [...]"

        if hasattr(row.created_at, "strftime"):
            created_str = row.created_at.strftime("%Y-%m-%d %H:%M")
        else:
            created_str = str(row.created_at)[:16]

        snippets.append(f"[{created_str}] {row.role}: {content}")

    return "\n---\n".join(snippets)


async def get_branch_summary(
    chat_id: str,
    branch_name: str,
    session: Optional[AsyncSession] = None,
) -> str:
    """Фасад извлечения последнего инкрементального среза слияния ветки."""
    if session is None:
        raise ValueError("Требуется активная асинхронная сессия БД.")

    event = await get_latest_merge_event(
        chat_id=chat_id,
        branch_name=branch_name,
        session=session,
    )
    if not event:
        return f"Сводка для ветки '{branch_name}' не найдена."

    return f"Сводка ветки '{branch_name}': {event.content}"