import pytest

from src.schemas import BranchMergeResponse, BranchResponse, MessageResponse, RoleEnum
from tests.conftest import append_api_message, create_api_chat


@pytest.mark.asyncio
async def test_chat_turn_fork_and_squash_merge(api_client, api_gemini):
    chat, main = await create_api_chat(api_client)
    question = await append_api_message(api_client, main.id, "Main question")
    assert question.parent_id is None
    api_gemini.generate_step.assert_not_awaited()

    response = await api_client.post(f"/api/v1/branches/{main.id}/run", json={
        "expected_head_id": question.id,
    })
    assert response.status_code == 200, response.text
    answer = MessageResponse.model_validate(response.json())
    assert answer.role == RoleEnum.MODEL and answer.parent_id == question.id
    assert answer.metadata["execution_trace"]["steps"] == []
    assert "metadata_json" not in response.json()

    response = await api_client.post(f"/api/v1/chats/{chat.id}/branches", json={
        "name": "feature/refactor", "target_message_id": answer.id,
    })
    assert response.status_code == 201, response.text
    side = BranchResponse.model_validate(response.json())
    side_question = await append_api_message(api_client, side.id, "Investigate alternative")
    assert side_question.parent_id == answer.id
    response = await api_client.post(f"/api/v1/branches/{side.id}/run", json={})
    assert response.status_code == 200, response.text
    side_answer = MessageResponse.model_validate(response.json())
    assert side_answer.parent_id == side_question.id

    response = await api_client.post(f"/api/v1/branches/{side.id}/merge", json={})
    assert response.status_code == 200, response.text
    merge = BranchMergeResponse.model_validate(response.json())
    assert merge.compressed_messages_count == 2 and merge.target_branch == "main"
    response = await api_client.get(f"/api/v1/branches/{main.id}/timeline")
    assert response.status_code == 200, response.text
    timeline = [MessageResponse.model_validate(item) for item in response.json()]
    assert [item.id for item in timeline] == [question.id, answer.id, merge.merge_message_id]
    assert timeline[-1].role == RoleEnum.SYSTEM_MERGE
    assert timeline[-1].parent_id == answer.id and timeline[-1].content == merge.summary
    assert timeline[1].metadata == answer.metadata
    response = await api_client.get(f"/api/v1/chats/{chat.id}/branches")
    branches = {row["id"]: BranchResponse.model_validate(row) for row in response.json()}
    assert branches[main.id].head_message_id == merge.merge_message_id
    assert branches[side.id].head_message_id == branches[side.id].last_merged_message_id == side_answer.id
