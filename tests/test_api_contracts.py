from uuid import uuid4

import pytest
from httpx import AsyncClient

from src.config import settings
from src.agent.loop import run_turn
from src.schemas import (
    AncestorMutationWarningResponse, BranchResponse, ChatResponse,
    MessageResponse, RegenerateReplyResponse, ToggleActiveResponse,
)
from src.storage.crud.chats import append_message
from src.storage.models import MessageRole
from tests.conftest import append_api_message, create_api_chat

pytestmark = pytest.mark.asyncio


async def test_create_and_list_chats(api_client):
    chat, _ = await create_api_chat(api_client)
    response = await api_client.get("/api/v1/chats")
    assert response.status_code == 200
    assert [ChatResponse.model_validate(row) for row in response.json()] == [chat]


@pytest.mark.parametrize("name", ["bad name", "/side", "side/", "side//child"])
async def test_invalid_branch_name(api_client, name):
    chat, main = await create_api_chat(api_client)
    first = await append_api_message(api_client, main.id, "Question")
    response = await api_client.post(f"/api/v1/chats/{chat.id}/branches", json={
        "name": name, "target_message_id": first.id,
    })
    assert response.status_code == 422


@pytest.mark.parametrize("content", ["", "  "])
async def test_blank_message(api_client, api_gemini, content):
    _, main = await create_api_chat(api_client)
    response = await api_client.post(f"/api/v1/branches/{main.id}/messages", json={"content": content})
    assert response.status_code == 422
    api_gemini.generate_step.assert_not_awaited()


async def test_missing_and_duplicate_branch_targets(api_client):
    chat, main = await create_api_chat(api_client)
    url = f"/api/v1/chats/{chat.id}/branches"
    body = {"name": "feature/refactor", "target_message_id": str(uuid4())}
    assert (await api_client.post(url, json=body)).status_code == 404
    first = await append_api_message(api_client, main.id, "Question")
    body["target_message_id"] = first.id
    created = await api_client.post(url, json=body)
    assert created.status_code == 201
    assert BranchResponse.model_validate(created.json()).name == body["name"]
    assert (await api_client.post(url, json=body)).status_code == 409


async def test_edit_warning_force_and_mask(api_client: AsyncClient) -> None:
    """Проверить изменение сообщения, общего для двух веток.

    Форк делает исходную реплику общей. Правка требует подтверждения
    с числом затронутых веток, а после маскирования реплика исчезает
    из обычной истории main.
    """
    chat, main = await create_api_chat(api_client)
    first = await append_api_message(api_client, main.id, "Question")
    assert (await api_client.post(f"/api/v1/chats/{chat.id}/branches", json={
        "name": "side", "target_message_id": first.id,
    })).status_code == 201
    url = f"/api/v1/messages/{first.id}"
    warning = await api_client.patch(url, json={"content": "Edited"})
    assert warning.status_code == 409
    assert AncestorMutationWarningResponse.model_validate(warning.json()).affected_branch_count == 2
    edited = await api_client.patch(url, json={"content": "Edited", "force": True})
    assert edited.status_code == 200
    dto = MessageResponse.model_validate(edited.json())
    assert dto.is_edited and dto.content == "Edited"
    masked = await api_client.post(f"{url}/toggle-active")
    assert masked.status_code == 200
    assert ToggleActiveResponse.model_validate(masked.json()).is_active is False
    assert (await api_client.get(f"/api/v1/branches/{main.id}/timeline")).json() == []


async def test_timeline_includes_masked_messages_on_request(api_client: AsyncClient) -> None:
    """Проверить, что маскирование сохраняет место реплики и связи в истории.

    Скрываем среднюю из трёх реплик и сравниваем обычную и полную выдачу.
    После снятия маски средняя реплика возвращается в обычную выдачу
    на прежнее место.
    """
    _, main = await create_api_chat(api_client)
    first = await append_api_message(api_client, main.id, "First")
    hidden = await append_api_message(api_client, main.id, "Masked")
    latest = await append_api_message(api_client, main.id, "Latest")
    timeline_url = f"/api/v1/branches/{main.id}/timeline"
    toggle_url = f"/api/v1/messages/{hidden.id}/toggle-active"
    assert (await api_client.post(toggle_url)).status_code == 200

    for params in ({}, {"include_inactive": "false"}):
        active = await api_client.get(timeline_url, params=params)
        assert active.status_code == 200
        assert [row["id"] for row in active.json()] == [first.id, latest.id]

    full = await api_client.get(timeline_url, params={"include_inactive": "true"})
    assert full.status_code == 200
    messages = [MessageResponse.model_validate(row) for row in full.json()]
    assert [(msg.id, msg.is_active) for msg in messages] == [
        (first.id, True), (hidden.id, False), (latest.id, True),
    ]
    assert messages[1].content == hidden.content
    assert messages[2].parent_id == hidden.id

    assert (await api_client.post(toggle_url)).status_code == 200
    restored = await api_client.get(timeline_url)
    assert restored.status_code == 200
    assert [(row["id"], row["is_active"]) for row in restored.json()] == [
        (first.id, True), (hidden.id, True), (latest.id, True),
    ]


async def test_timeline_rejects_invalid_include_inactive(api_client):
    _, main = await create_api_chat(api_client)
    response = await api_client.get(
        f"/api/v1/branches/{main.id}/timeline", params={"include_inactive": "invalid"},
    )
    assert response.status_code == 422


async def test_run_excludes_messages_visible_only_in_full_timeline(
    api_client: AsyncClient, api_gemini,
) -> None:
    """Проверить разницу между полной историей и контекстом генерации.

    После чтения полной истории запускаем генерацию с активной вершины.
    В запросе к моку Gemini должна остаться только активная реплика.
    """
    _, main = await create_api_chat(api_client)
    hidden = await append_api_message(api_client, main.id, "Masked prompt")
    latest = await append_api_message(api_client, main.id, "Active prompt")
    assert (await api_client.post(f"/api/v1/messages/{hidden.id}/toggle-active")).status_code == 200

    url = f"/api/v1/branches/{main.id}"
    full = await api_client.get(f"{url}/timeline", params={"include_inactive": "true"})
    assert full.status_code == 200
    assert [row["id"] for row in full.json()] == [hidden.id, latest.id]

    run = await api_client.post(f"{url}/run", json={"expected_head_id": latest.id})
    assert run.status_code == 200
    api_gemini.generate_step.assert_awaited_once()
    contents = api_gemini.generate_step.await_args.kwargs["contents"]
    assert [part.text for message in contents for part in message.parts] == [latest.content]


async def test_merge_empty_delta(api_client, api_gemini):
    chat, main = await create_api_chat(api_client)
    first = await append_api_message(api_client, main.id, "Question")
    created = await api_client.post(f"/api/v1/chats/{chat.id}/branches", json={
        "name": "side", "target_message_id": first.id,
    })
    assert created.status_code == 201
    assert (await api_client.post(f"/api/v1/branches/{created.json()['id']}/merge", json={})).status_code == 400
    api_gemini.summarize_delta.assert_not_awaited()


async def test_run_rejects_stale_head(api_client, api_gemini):
    _, main = await create_api_chat(api_client)
    first = await append_api_message(api_client, main.id, "Question")
    response = await api_client.post(f"/api/v1/branches/{main.id}/run", json={"expected_head_id": str(uuid4())})
    assert response.status_code == 409
    api_gemini.generate_step.assert_not_awaited()
    history = await api_client.get(f"/api/v1/branches/{main.id}/timeline")
    assert [item["id"] for item in history.json()] == [first.id]


async def test_run_rechecks_head_after_service_validation(
    api_client: AsyncClient, api_gemini, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Смоделировать смену вершины между проверкой в сервисе и запуском ядра.

    Обёртка над run_turn добавляет новую реплику после проверки исходного
    expected_head_id. API должен вернуть 409 до обращения к Gemini.
    """
    _, main = await create_api_chat(api_client)
    first = await append_api_message(api_client, main.id, "Question")

    async def concurrent_append(*args, **kwargs):
        await append_api_message(api_client, main.id, "New question")
        return await run_turn(*args, **kwargs)

    monkeypatch.setattr("src.services.run_turn", concurrent_append)
    response = await api_client.post(f"/api/v1/branches/{main.id}/run", json={
        "expected_head_id": first.id,
    })
    assert response.status_code == 409
    api_gemini.generate_step.assert_not_awaited()


async def test_missing_chat_branch_and_message(api_client):
    missing = uuid4()
    for url in (f"/chats/{missing}/branches", f"/branches/{missing}/timeline"):
        response = await api_client.get(f"/api/v1{url}")
        assert response.status_code == 404
        assert response.json()["detail"]
    response = await api_client.patch(f"/api/v1/messages/{missing}", json={"content": "Edit"})
    assert response.status_code == 404


async def test_run_requires_active_user_head(api_client, api_gemini):
    _, main = await create_api_chat(api_client)
    run_url = f"/api/v1/branches/{main.id}/run"
    assert (await api_client.post(run_url, json={})).status_code == 409
    first = await append_api_message(api_client, main.id, "Question")
    toggle_url = f"/api/v1/messages/{first.id}/toggle-active"
    assert (await api_client.post(toggle_url)).status_code == 200
    assert (await api_client.post(run_url, json={})).status_code == 409
    api_gemini.generate_step.assert_not_awaited()


async def test_run_checks_active_question_separately_from_head(api_client: AsyncClient, api_gemini) -> None:
    """Пропустить скрытую вершину, сохранив её в цепочке и проверке записи."""
    _, main = await create_api_chat(api_client)
    question = await append_api_message(api_client, main.id, "Question")
    hidden = await append_api_message(api_client, main.id, "Hidden head")
    branch_url = f"/api/v1/branches/{main.id}"
    assert (await api_client.post(f"/api/v1/messages/{hidden.id}/toggle-active")).status_code == 200
    run = await api_client.post(f"{branch_url}/run", json={
        "expected_head_id": hidden.id, "expected_question_id": hidden.id,
    })
    assert run.status_code == 409
    api_gemini.generate_step.assert_not_awaited()

    run = await api_client.post(f"{branch_url}/run", json={
        "expected_head_id": hidden.id, "expected_question_id": question.id,
    })
    assert run.status_code == 200, run.text
    answer = MessageResponse.model_validate(run.json())
    assert answer.parent_id == hidden.id
    assert answer.metadata["reply_to_message_id"] == question.id
    assert (await api_client.post(f"{branch_url}/run", json={})).status_code == 409

    # Hiding the recorded question must not redirect regeneration to another ancestor.
    assert (await api_client.post(f"/api/v1/messages/{question.id}/toggle-active")).status_code == 200
    response = await api_client.post(f"{branch_url}/regenerate", json={
        "expected_head_id": answer.id, "new_branch_name": "retry",
    })
    assert response.status_code == 409
    assert "Верните" in response.json()["detail"]
    assert (await api_client.post(f"/api/v1/messages/{question.id}/toggle-active")).status_code == 200
    suffix = await append_api_message(api_client, main.id, "Hidden suffix")
    assert (await api_client.post(f"/api/v1/messages/{suffix.id}/toggle-active")).status_code == 200
    response = await api_client.post(f"{branch_url}/regenerate", json={
        "expected_head_id": suffix.id, "new_branch_name": "retry",
    })
    assert response.status_code == 201, response.text
    result = RegenerateReplyResponse.model_validate(response.json())
    assert result.branch.forked_from_id == question.id


@pytest.mark.parametrize("summary_fits", [True, False])
async def test_run_keeps_pending_question_after_merge(api_client, api_gemini, monkeypatch, summary_fits):
    """Сохранить вопрос в окне, даже если более новая выжимка превышает бюджет."""
    chat, main = await create_api_chat(api_client)
    question = await append_api_message(api_client, main.id, "Pending question")
    fork = await api_client.post(f"/api/v1/chats/{chat.id}/branches", json={
        "name": "side", "target_message_id": question.id,
    })
    assert fork.status_code == 201
    side = BranchResponse.model_validate(fork.json())
    await append_api_message(api_client, side.id, "Side delta")
    summary = "Merge summary" if summary_fits else "Merge summary " * 200
    api_gemini.summarize_delta.return_value = summary
    merge = await api_client.post(f"/api/v1/branches/{side.id}/merge", json={})
    assert merge.status_code == 200, merge.text
    hidden = await append_api_message(api_client, main.id, "Masked after merge")
    assert (await api_client.post(f"/api/v1/messages/{hidden.id}/toggle-active")).status_code == 200
    monkeypatch.setattr(settings, "MAX_CONTEXT_BYTES", 1200)
    run = await api_client.post(f"/api/v1/branches/{main.id}/run", json={
        "expected_head_id": hidden.id, "expected_question_id": question.id,
    })
    assert run.status_code == 200, run.text
    assert run.json()["parent_id"] == hidden.id
    contents = api_gemini.generate_step.await_args.kwargs["contents"]
    sent = " ".join(part.text for content in contents for part in content.parts)
    assert question.content in sent
    assert ("Merge summary" in sent) is summary_fits
    assert hidden.content not in sent


async def test_lifespan_initializes_sqlite_and_cors(monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from src import main
    from src.storage import database

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(database, "async_session_factory", async_sessionmaker(
        engine, expire_on_commit=False, autoflush=False,
    ))
    async with main.app.router.lifespan_context(main.app):
        async with AsyncClient(
            transport=ASGITransport(app=main.app), base_url="http://test",
        ) as client:
            await create_api_chat(client)
            response = await client.options("/api/v1/chats", headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            })
            assert response.status_code == 200
            assert response.headers["access-control-allow-origin"] == "*"


async def test_run_maps_oversize_to_413(api_client, api_gemini, monkeypatch):
    _, main = await create_api_chat(api_client)
    first = await append_api_message(api_client, main.id, "Question")
    monkeypatch.setattr(settings, "MAX_CONTEXT_BYTES", 1)
    response = await api_client.post(f"/api/v1/branches/{main.id}/run", json={})
    assert response.status_code == 413
    api_gemini.generate_step.assert_not_awaited()
    assert (await api_client.get(f"/api/v1/branches/{main.id}/timeline")).json()[0]["id"] == first.id


async def test_regenerate_validates_head_and_preserves_original_branch(api_client):
    _, main = await create_api_chat(api_client)
    first = await append_api_message(api_client, main.id, "Question")
    run = await api_client.post(f"/api/v1/branches/{main.id}/run", json={})
    assert run.status_code == 200
    url = f"/api/v1/branches/{main.id}/regenerate"
    body = {"new_branch_name": "retry/one"}
    assert (await api_client.post(url, json=body)).status_code == 422
    assert (await api_client.post(url, json={**body, "expected_head_id": first.id})).status_code == 409
    response = await api_client.post(url, json={**body, "expected_head_id": run.json()["id"]})
    assert response.status_code == 201, response.text
    dto = RegenerateReplyResponse.model_validate(response.json())
    assert dto.branch.forked_from_id == first.id
    assert dto.branch.head_message_id == dto.message.id
    assert dto.message.parent_id == first.id
    original = await api_client.get(f"/api/v1/branches/{main.id}/timeline")
    assert original.json()[-1]["id"] == run.json()["id"]


async def test_regenerate_uses_direct_parent_for_legacy_answer(api_client, file_sessions):
    chat, main = await create_api_chat(api_client)
    question = await append_api_message(api_client, main.id, "Legacy question")
    async with file_sessions.begin() as session:
        answer = await append_message(chat.id, main.name, MessageRole.MODEL, "Legacy answer", session)
        answer_id = answer.id

    response = await api_client.post(f"/api/v1/branches/{main.id}/regenerate", json={
        "expected_head_id": answer_id, "new_branch_name": "retry/legacy",
    })
    assert response.status_code == 201, response.text
    assert response.json()["branch"]["forked_from_id"] == question.id


async def test_regenerate_failure_exposes_fork_for_retry(
    api_client: AsyncClient, api_gemini,
) -> None:
    """Проверить, что сбой Gemini оставляет форк пригодным для повтора.

    После ответа 502 берём ID форка и его вершины из ответа API.
    Повторяем генерацию для сохранённого вопроса через /run.
    """
    _, main = await create_api_chat(api_client)
    first = await append_api_message(api_client, main.id, "Question")
    run = await api_client.post(f"/api/v1/branches/{main.id}/run", json={})
    assert run.status_code == 200
    api_gemini.generate_step.side_effect = RuntimeError("upstream diagnostic")
    response = await api_client.post(f"/api/v1/branches/{main.id}/regenerate", json={
        "new_branch_name": "retry", "expected_head_id": run.json()["id"],
    })
    assert response.status_code == 502
    assert "upstream diagnostic" not in response.text
    assert response.json()["head_message_id"] == first.id
    api_gemini.generate_step.side_effect = None
    retry = await api_client.post(f"/api/v1/branches/{response.json()['branch_id']}/run", json={
        "expected_head_id": first.id,
    })
    assert retry.status_code == 200
    assert retry.json()["parent_id"] == first.id
