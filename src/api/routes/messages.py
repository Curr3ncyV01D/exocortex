from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header

from src import services
from src.api.deps import SessionDep
from src.schemas import (
    AncestorMutationWarningResponse,
    ErrorResponse,
    MessageResponse,
    MessageUpdateContentRequest,
    SendMessageRequest,
    MessageActiveRequest,
    MessageActiveResponse,
)

router = APIRouter(tags=["messages"], responses={404: {"model": ErrorResponse}})


@router.post(
    "/branches/{branch_id}/messages", response_model=MessageResponse, status_code=201,
    responses={409: {"model": ErrorResponse}},
)
async def send_message(
    branch_id: str,
    payload: SendMessageRequest,
    session: SessionDep,
    idempotency_key: Annotated[UUID | None, Header()] = None,
):
    key = str(idempotency_key) if idempotency_key is not None else None
    return await services.send_message(session, branch_id, payload.content, key)


@router.patch(
    "/messages/{message_id}", response_model=MessageResponse,
    responses={409: {"model": AncestorMutationWarningResponse}},
)
async def edit_message(message_id: str, payload: MessageUpdateContentRequest, session: SessionDep):
    return await services.edit_message(session, message_id, payload.content, payload.force)


@router.put("/messages/{message_id}/active", response_model=MessageActiveResponse)
async def set_message_active(message_id: str, payload: MessageActiveRequest, session: SessionDep):
    return await services.set_message_active_state(session, message_id, payload.is_active)
