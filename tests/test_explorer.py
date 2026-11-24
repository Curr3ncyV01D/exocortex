"""Exercise the compact graph contract, provenance and consistent snapshots."""

from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src import services
from src.storage.crud.chats import append_message
from src.storage.crud.graph import branch_from_here, squash_merge_branch
from src.storage.models import Branch, BranchStatus, MergeEvent, Message, MessageRole
from tests.conftest import append_api_message, create_api_chat, create_initial_chat

pytestmark = pytest.mark.asyncio


async def test_graph_empty_and_missing_chat(api_client: AsyncClient, api_gemini: object) -> None:
    """An empty existing chat retains main; a missing chat is a normal 404."""
    chat, main = await create_api_chat(api_client)
    response = await api_client.get(f"/api/v1/chats/{chat.id}/graph")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "chat_id": chat.id, "nodes": [], "branches": [main.model_dump(mode="json")], "merges": [],
    }
    missing = await api_client.get(f"/api/v1/chats/{uuid4()}/graph")
    assert missing.status_code == 404
    api_gemini.generate_step.assert_not_awaited()
    api_gemini.summarize_delta.assert_not_awaited()


async def test_graph_is_compact_complete_unique_and_chat_scoped(
    api_client: AsyncClient, file_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Keep hidden, shared and unassigned nodes while excluding other chats and full payloads."""
    chat, main = await create_api_chat(api_client)
    other, other_main = await create_api_chat(api_client)
    foreign = await append_api_message(api_client, other_main.id, "Foreign")
    first = await append_api_message(api_client, main.id, "First")
    body = "  **Текст**\n\t" + "я" * 230
    last = await append_api_message(api_client, main.id, body)
    fork = await api_client.post(f"/api/v1/chats/{chat.id}/branches", json={
        "name": "исследование/ветка", "target_message_id": first.id,
    })
    assert fork.status_code == 201
    orphan_id = str(uuid4())
    timestamp = datetime(2020, 1, 2, 3, 4, tzinfo=timezone.utc)
    async with file_sessions() as session:
        async with session.begin():
            message = await session.get(Message, last.id)
            message.is_active = False
            message.is_edited = True
            message.updated_at = timestamp
            message.metadata_json = '{"private_trace":"DO_NOT_EXPOSE"}'
            message.created_at = timestamp  # Parent links, not timestamps, define history.
            branch = await session.get(Branch, fork.json()["id"])
            branch.status = BranchStatus.ARCHIVED
            session.add(Message(id=orphan_id, chat_id=chat.id, role=MessageRole.USER, content="Unassigned"))
    response = await api_client.get(f"/api/v1/chats/{chat.id}/graph")
    assert response.status_code == 200, response.text
    graph = response.json()
    assert set(graph) == {"chat_id", "nodes", "branches", "merges"}
    assert len(graph["nodes"]) == 3
    nodes = {node["id"]: node for node in graph["nodes"]}
    assert set(nodes) == {first.id, last.id, orphan_id}
    assert foreign.id not in nodes
    assert nodes[last.id] == {
        "id": last.id, "parent_id": first.id, "role": "user",
        "preview": ("**Текст** " + "я" * 230)[:200], "preview_truncated": True, "is_active": False,
        "is_edited": True, "created_at": "2020-01-02T03:04:00Z", "updated_at": "2020-01-02T03:04:00Z",
    }
    assert "DO_NOT_EXPOSE" not in response.text and body not in response.text
    assert {branch["id"] for branch in graph["branches"]} == {main.id, fork.json()["id"]}
    assert next(branch for branch in graph["branches"] if branch["name"] != "main")["status"] == "archived"
    again = await api_client.get(f"/api/v1/chats/{chat.id}/graph")
    assert again.json() == graph


async def test_merge_provenance_preserves_hidden_head_and_each_merge_boundary(
    session: AsyncSession, initial_chat_setup: dict[str, str],
) -> None:
    """Each merge records its own actual HEAD and rolls back with the surrounding transaction."""
    chat_id = initial_chat_setup["chat_id"]
    fork = await branch_from_here(chat_id, initial_chat_setup["root_message_id"], "side", session)
    await append_message(chat_id, "side", MessageRole.USER, "Included", session)
    hidden = await append_message(chat_id, "side", MessageRole.MODEL, "Hidden boundary", session)
    fork_id = fork.id
    hidden_id = hidden.id
    hidden.is_active = False
    await session.commit()
    summarizer = AsyncMock(return_value="Summary one")
    first = await squash_merge_branch(chat_id, "side", summarizer_func=summarizer, session=session)
    event = await session.get(MergeEvent, first["merge_message_id"])
    assert event.source_head_message_id == hidden.id
    assert event.compressed_messages_count == 1
    assert [message.id for message in summarizer.await_args.args[0]] != [hidden.id]
    await session.commit()
    next_message = await append_message(chat_id, "side", MessageRole.USER, "Next", session)
    next_id = next_message.id
    second = await squash_merge_branch(chat_id, "side", summarizer_func=AsyncMock(return_value="Summary two"), session=session)
    second_event = await session.get(MergeEvent, second["merge_message_id"])
    assert second_event.source_head_message_id == next_message.id
    assert event.source_head_message_id == hidden.id
    await session.commit()
    snapshot = await services.get_chat_graph(session, chat_id)
    assert {merge.source_head_message_id for merge in snapshot.merges} == {hidden.id, next_message.id}
    assert {node.id for node in snapshot.nodes} >= {first["merge_message_id"], second["merge_message_id"]}
    await append_message(chat_id, "side", MessageRole.USER, "Rolled back", session)
    rolled_back = await squash_merge_branch(chat_id, "side", summarizer_func=AsyncMock(return_value="Discard"), session=session)
    rolled_back_id = rolled_back["merge_message_id"]
    await session.rollback()
    assert await session.get(MergeEvent, rolled_back_id) is None
    assert await session.scalar(select(Branch.last_merged_message_id).where(Branch.id == fork_id)) == next_id


async def test_failed_summary_does_not_save_provenance(
    session: AsyncSession, initial_chat_setup: dict[str, str],
) -> None:
    chat_id = initial_chat_setup["chat_id"]
    await branch_from_here(chat_id, initial_chat_setup["root_message_id"], "side", session)
    await append_message(chat_id, "side", MessageRole.USER, "Question", session)
    await session.commit()
    with pytest.raises(RuntimeError, match="provider failure"):
        await squash_merge_branch(chat_id, "side", session=session,
                                  summarizer_func=AsyncMock(side_effect=RuntimeError("provider failure")))
    await session.rollback()
    assert list(await session.scalars(select(MergeEvent))) == []


async def test_provenance_foreign_key_rejects_another_chat(
    session: AsyncSession, initial_chat_setup: dict[str, str],
) -> None:
    """The database, rather than only HTTP validation, enforces provenance isolation."""
    first_chat = initial_chat_setup["chat_id"]
    second = await create_initial_chat(session)
    session.add(MergeEvent(chat_id=first_chat, role=MessageRole.SYSTEM_MERGE, content="Summary",
                           source_branch_name="side", forked_from_msg_id=initial_chat_setup["root_message_id"],
                           source_head_message_id=second["root_message_id"], compressed_messages_count=1))
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


@pytest.mark.parametrize("corruption", ["cycle", "parent", "branch", "merge"])
async def test_graph_integrity_failures_return_safe_500(
    api_client: AsyncClient, file_sessions: async_sessionmaker[AsyncSession], corruption: str,
) -> None:
    """Broken stored links must not silently produce a plausible but false graph."""
    chat, main = await create_api_chat(api_client)
    root = await append_api_message(api_client, main.id, "private text")
    async with file_sessions() as session:
        await session.execute(text("PRAGMA foreign_keys=OFF"))
        if corruption == "cycle":
            await session.execute(text("UPDATE messages SET parent_id=id WHERE id=:id"), {"id": root.id})
        elif corruption == "parent":
            await session.execute(text("UPDATE messages SET parent_id='missing' WHERE id=:id"), {"id": root.id})
        elif corruption == "branch":
            await session.execute(text("UPDATE branches SET head_message_id='missing' WHERE id=:id"), {"id": main.id})
        else:
            await session.execute(text("UPDATE messages SET role='SYSTEM_MERGE' WHERE id=:id"), {"id": root.id})
        await session.commit()
    response = await api_client.get(f"/api/v1/chats/{chat.id}/graph")
    assert response.status_code == 500
    assert response.json() == {"detail": "Нарушена целостность графа."}


async def test_graph_reads_one_snapshot_during_concurrent_write(
    file_sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A committed write between graph queries cannot mix old nodes with a new branch HEAD."""
    async with file_sessions() as setup:
        initial = await create_initial_chat(setup)
    async with file_sessions() as reader:
        original = reader.execute
        written = False

        async def execute(*args: object, **kwargs: object):
            nonlocal written
            result = await original(*args, **kwargs)
            if not written and str(args[0]).lstrip().startswith("SELECT") and "messages" in str(args[0]):
                written = True
                async with file_sessions() as writer:
                    await append_message(initial["chat_id"], "main", MessageRole.USER, "Concurrent", writer)
                    await writer.commit()
            return result

        monkeypatch.setattr(reader, "execute", execute)
        snapshot = await services.get_chat_graph(reader, initial["chat_id"])
        assert written
        assert [node.id for node in snapshot.nodes] == [initial["root_message_id"]]
        assert snapshot.branches[0].head_message_id == initial["root_message_id"]
    async with file_sessions() as reader:
        current = await services.get_chat_graph(reader, initial["chat_id"])
        assert len(current.nodes) == 2
