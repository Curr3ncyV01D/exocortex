"""Specify bounded history windows independently of the legacy full timeline."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import event, insert, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src import services
from src.storage.crud.chats import append_message
from src.storage.models import Branch, Chat, Message, MessageRole, MergeEvent

pytestmark = pytest.mark.asyncio


async def seed_history(factory: async_sessionmaker[AsyncSession], count: int = 101) -> tuple[str, str, list[str]]:
    """Create a linked history whose timestamps intentionally run backwards."""
    chat_id, branch_id = str(uuid4()), str(uuid4())
    identities = [str(uuid4()) for _ in range(count)]
    async with factory() as session:
        async with session.begin():
            session.add(Chat(id=chat_id, title="Window history"))
            await session.flush()
            if identities:
                await session.execute(insert(Message.__table__), [{
                    "id": identity, "chat_id": chat_id, "parent_id": identities[index - 1] if index else None,
                    "role": MessageRole.USER, "content": f"Message {index}", "is_active": True,
                    "is_edited": False, "created_at": datetime(2020, 1, 1, tzinfo=timezone.utc) - timedelta(minutes=index),
                } for index, identity in enumerate(identities)])
            session.add(Branch(id=branch_id, chat_id=chat_id, name="main", head_message_id=identities[-1] if identities else None))
    return chat_id, branch_id, identities


async def test_window_modes_and_boundaries(api_client: AsyncClient, file_sessions) -> None:
    """Tail, inclusive around and exclusive cursors all follow parents rather than dates."""
    _, branch, ids = await seed_history(file_sessions)
    url = f"/api/v1/branches/{branch}/timeline/window"
    cases = [({}, ids[-40:]), ({"mode": "around", "anchor_id": ids[50]}, ids[30:71]),
             ({"mode": "older", "anchor_id": ids[30]}, ids[:30]),
             ({"mode": "newer", "anchor_id": ids[70]}, ids[71:]),
             ({"mode": "around", "anchor_id": ids[0]}, ids[:21]),
             ({"mode": "around", "anchor_id": ids[-1], "radius": 0}, ids[-1:])]
    for query, expected in cases:
        response = await api_client.get(url, params=query)
        assert response.status_code == 200, response.text
        body = response.json()
        assert [message["id"] for message in body["messages"]] == expected
        assert body["branch"]["head_message_id"] == ids[-1]
        assert body["last_active_dialogue"] == {"id": ids[-1], "role": "user"}
        assert body["run_target"] == {"expected_head_id": ids[-1], "expected_question_id": ids[-1]}
        assert body["has_older"] is (expected[0] != ids[0])
        assert body["has_newer"] is (expected[-1] != ids[-1])
        assert body["older_cursor"] == (expected[0] if body["has_older"] else None)
        assert body["newer_cursor"] == (expected[-1] if body["has_newer"] else None)
    legacy = await api_client.get(f"/api/v1/branches/{branch}/timeline")
    assert legacy.status_code == 200
    assert [message["id"] for message in legacy.json()] == ids


async def test_empty_windows_and_missing_anchor(api_client: AsyncClient, file_sessions, api_gemini) -> None:
    _, empty, _ = await seed_history(file_sessions, 0)
    _, branch, ids = await seed_history(file_sessions, 1)
    for identity, query in [(empty, {}), (branch, {"mode": "older", "anchor_id": ids[0]}),
                            (branch, {"mode": "newer", "anchor_id": ids[0]})]:
        response = await api_client.get(f"/api/v1/branches/{identity}/timeline/window", params=query)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["messages"] == []
        assert body["has_older"] is False and body["has_newer"] is False
        assert body["older_cursor"] is None and body["newer_cursor"] is None
    assert (await api_client.get(f"/api/v1/branches/{uuid4()}/timeline/window")).status_code == 404
    assert (await api_client.get(f"/api/v1/branches/{empty}/timeline/window", params={"mode": "around", "anchor_id": ids[0]})).status_code == 404
    api_gemini.generate_step.assert_not_awaited()


@pytest.mark.parametrize("query", [
    {"mode": "invalid"}, {"mode": "around"}, {"mode": "older"}, {"mode": "newer"},
    {"anchor_id": "x"}, {"radius": 20}, {"limit": 0}, {"limit": 81},
    {"mode": "around", "anchor_id": "x", "limit": 40},
    {"mode": "around", "anchor_id": "x", "radius": -1},
    {"mode": "around", "anchor_id": "x", "radius": 41},
    {"mode": "older", "anchor_id": "x", "radius": 1},
    {"mode": "around", "anchor_id": ""},
])
async def test_window_parameter_validation(api_client: AsyncClient, file_sessions, query: dict[str, object]) -> None:
    _, branch, _ = await seed_history(file_sessions, 0)
    assert (await api_client.get(f"/api/v1/branches/{branch}/timeline/window", params=query)).status_code == 422


async def test_window_scope_and_true_run_target(api_client: AsyncClient, file_sessions) -> None:
    """Hidden HEAD and active summaries preserve the actual pending question outside the viewed slice."""
    chat_id, branch, ids = await seed_history(file_sessions, 50)
    _, _, foreign = await seed_history(file_sessions, 1)
    async with file_sessions() as session:
        async with session.begin():
            await session.execute(update(Message).where(Message.id.in_(ids[46:])).values(is_active=False))
            await session.execute(update(Message).where(Message.id == ids[45]).values(role=MessageRole.SYSTEM_MERGE))
            await session.execute(insert(MergeEvent.__table__), {"message_id": ids[45], "chat_id": chat_id,
                "source_branch_name": "side", "forked_from_msg_id": ids[0], "compressed_messages_count": 1})
            session.add(Branch(chat_id=chat_id, name="side", head_message_id=ids[10], forked_from_id=ids[10]))
    response = await api_client.get(f"/api/v1/branches/{branch}/timeline/window", params={"mode": "around", "anchor_id": ids[5], "radius": 1})
    body = response.json()
    assert response.status_code == 200, response.text
    assert [message["id"] for message in body["messages"]] == ids[4:7]
    assert body["run_target"] == {"expected_head_id": ids[-1], "expected_question_id": ids[44]}
    tail = (await api_client.get(f"/api/v1/branches/{branch}/timeline/window", params={"limit": 8})).json()
    assert len(tail["messages"]) == 8
    assert tail["messages"][-1]["is_active"] is False
    side = (await api_client.get(f"/api/v1/chats/{chat_id}/branches")).json()
    side_id = next(row["id"] for row in side if row["name"] == "side")
    for other in [foreign[0], ids[20]]:
        assert (await api_client.get(f"/api/v1/branches/{side_id}/timeline/window", params={"mode": "around", "anchor_id": other})).status_code == 404
    async with file_sessions() as session:
        async with session.begin():
            await session.execute(update(Message).where(Message.id == ids[44]).values(role=MessageRole.MODEL))
    model = (await api_client.get(f"/api/v1/branches/{branch}/timeline/window")).json()
    assert model["run_target"] is None
    assert model["last_active_dialogue"] == {"id": ids[44], "role": "model"}


@pytest.mark.parametrize("corruption", ["cycle", "missing", "foreign"])
async def test_window_rejects_corrupt_paths(api_client: AsyncClient, file_sessions, corruption: str) -> None:
    _, branch, ids = await seed_history(file_sessions, 3)
    _, _, foreign = await seed_history(file_sessions, 1)
    parent = ids[-1] if corruption == "cycle" else foreign[0] if corruption == "foreign" else "missing"
    async with file_sessions() as session:
        await session.execute(text("PRAGMA foreign_keys=OFF"))
        await session.execute(text("UPDATE messages SET parent_id=:parent WHERE id=:id"), {"parent": parent, "id": ids[0]})
        await session.commit()
    result = await api_client.get(f"/api/v1/branches/{branch}/timeline/window", params={"limit": 1})
    assert result.status_code == 500
    assert result.json() == {"detail": "Нарушена целостность графа."}


async def test_window_loads_only_bounded_payloads(file_sessions) -> None:
    """Inspect SQL so compact path traversal cannot accidentally load every full message."""
    _, branch, _ = await seed_history(file_sessions, 605)
    async with file_sessions() as session:
        engine = session.bind.sync_engine
        payloads: list[tuple[str, tuple]] = []
        def capture(connection, cursor, statement, parameters, context, many):
            if statement.lstrip().upper().startswith("SELECT") and "messages.content" in statement:
                payloads.append((statement, parameters))
        event.listen(engine, "before_cursor_execute", capture)
        try:
            result = await services.get_timeline_window(session, branch, mode="tail", limit=40)
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        assert len(result.messages) == 40
        assert payloads and all(" IN (" in sql and len(parameters) <= 82 for sql, parameters in payloads)


async def test_window_keeps_one_read_snapshot(file_sessions, monkeypatch: pytest.MonkeyPatch) -> None:
    chat_id, branch, ids = await seed_history(file_sessions, 3)
    async with file_sessions() as reader:
        execute = reader.execute
        written = False
        async def interleave(*args, **kwargs):
            nonlocal written
            result = await execute(*args, **kwargs)
            if not written and str(args[0]).lstrip().startswith("SELECT") and "messages" in str(args[0]):
                written = True
                async with file_sessions() as writer:
                    await append_message(chat_id, "main", MessageRole.USER, "Concurrent", writer)
                    await writer.commit()
            return result
        monkeypatch.setattr(reader, "execute", interleave)
        result = await services.get_timeline_window(reader, branch, mode="tail", limit=40)
        assert written
        assert [message.id for message in result.messages] == ids
        assert result.branch.head_message_id == ids[-1]
        assert result.run_target.expected_head_id == ids[-1]
    async with file_sessions() as reader:
        current = await services.get_timeline_window(reader, branch, mode="tail", limit=40)
        assert len(current.messages) == 4
