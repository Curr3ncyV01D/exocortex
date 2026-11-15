from typing import Annotated

from fastapi import APIRouter, Query

from src import services
from src.api.deps import GeminiDep, SessionDep
from src.schemas import (
    BranchCreateRequest,
    BranchMergeRequest,
    BranchMergeResponse,
    BranchResponse,
    ErrorResponse,
    MessageResponse,
    RegenerateReplyRequest,
    RegenerateReplyResponse,
    RegenerationFailureResponse,
    RunTurnRequest,
)

router = APIRouter(tags=["branches"], responses={404: {"model": ErrorResponse}})


@router.get("/chats/{chat_id}/branches", response_model=list[BranchResponse])
async def list_branches(chat_id: str, session: SessionDep):
    return await services.list_branches(session, chat_id)


@router.post(
    "/chats/{chat_id}/branches", response_model=BranchResponse, status_code=201,
    responses={409: {"model": ErrorResponse}},
)
async def create_branch(chat_id: str, payload: BranchCreateRequest, session: SessionDep):
    return await services.create_branch(session, chat_id, payload.target_message_id, payload.name)


@router.get("/branches/{branch_id}/timeline", response_model=list[MessageResponse])
async def get_timeline(
    branch_id: str,
    session: SessionDep,
    include_inactive: Annotated[bool, Query(description="Включить скрытые из контекста сообщения")] = False,
):
    return await services.get_timeline(session, branch_id, include_inactive=include_inactive)


@router.post(
    "/branches/{branch_id}/merge", response_model=BranchMergeResponse,
    responses={code: {"model": ErrorResponse} for code in (400, 409, 502)},
)
async def merge_branch(
    branch_id: str, payload: BranchMergeRequest, session: SessionDep, client: GeminiDep,
):
    return await services.merge_branch(session, client, branch_id, payload.target_branch)


@router.post(
    "/branches/{branch_id}/run", response_model=MessageResponse,
    responses={code: {"model": ErrorResponse} for code in (409, 413, 502)},
)
async def run_branch(branch_id: str, payload: RunTurnRequest, session: SessionDep, client: GeminiDep):
    return await services.generate_reply(session, client, branch_id, payload.expected_head_id)


@router.post(
    "/branches/{branch_id}/regenerate", response_model=RegenerateReplyResponse, status_code=201,
    responses={
        409: {"model": ErrorResponse},
        413: {"model": RegenerationFailureResponse},
        502: {"model": RegenerationFailureResponse},
    },
)
async def regenerate_branch(
    branch_id: str, payload: RegenerateReplyRequest, session: SessionDep, client: GeminiDep,
):
    branch, message = await services.regenerate_reply(
        session, client, branch_id, str(payload.expected_head_id), payload.new_branch_name,
    )
    return RegenerateReplyResponse(
        branch=BranchResponse.model_validate(branch),
        message=MessageResponse.model_validate(message),
    )
