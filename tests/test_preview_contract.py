"""Check exact preview truncation semantics at the API boundary."""

import pytest
from httpx import AsyncClient
from tests.conftest import create_api_chat, append_api_message


@pytest.mark.asyncio
@pytest.mark.parametrize(("content", "expected", "truncated"), [
    ("я" * 199, "я" * 199, False),
    ("я" * 200, "я" * 200, False),
    ("я" * 201, "я" * 200, True),
    ("  слово\n\t " + "я" * 195, "слово " + "я" * 194, True),
    ("x" + " " * 250 + "y", "x y", False),
])
async def test_preview_truncation_follows_normalized_length(
    api_client: AsyncClient, content: str, expected: str, truncated: bool,
) -> None:
    """A 200-character message must not be confused with a truncated longer one."""
    chat, branch = await create_api_chat(api_client)
    saved = await append_api_message(api_client, branch.id, content)
    response = await api_client.get(f"/api/v1/chats/{chat.id}/graph")
    assert response.status_code == 200
    (node,) = response.json()["nodes"]
    assert node["id"] == saved.id
    assert node["preview"] == expected
    assert node["preview_truncated"] is truncated
    history = await api_client.get(f"/api/v1/branches/{branch.id}/timeline")
    assert history.json()[0]["content"] == content
