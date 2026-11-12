from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.storage.models import MergeEvent


async def get_latest_merge_event(
    chat_id: str,
    branch_name: str,
    session: AsyncSession,
) -> Optional[MergeEvent]:
    """Возвращает последний хронологический узел слияния для указанной ветки."""
    stmt = (
        select(MergeEvent)
        .where(
            MergeEvent.chat_id == chat_id,
            MergeEvent.source_branch_name == branch_name,
        )
        .order_by(MergeEvent.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()