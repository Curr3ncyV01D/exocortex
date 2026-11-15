from fastapi import APIRouter

from src import services
from src.api.deps import SessionDep
from src.schemas import ChatCreateRequest, ChatResponse

router = APIRouter(prefix="/chats", tags=["chats"])


@router.post("", response_model=ChatResponse, status_code=201)
async def create_chat(payload: ChatCreateRequest, session: SessionDep):
    return await services.create_chat(session, payload.title)


@router.get("", response_model=list[ChatResponse])
async def list_chats(session: SessionDep):
    return await services.list_chats(session)
