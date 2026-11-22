from fastapi import APIRouter

from src import services
from src.api.deps import SessionDep
from src.schemas import ChatCreateRequest, ChatResponse, ChatGraphResponse, ErrorResponse

router = APIRouter(prefix="/chats", tags=["chats"])


@router.post("", response_model=ChatResponse, status_code=201)
async def create_chat(payload: ChatCreateRequest, session: SessionDep):
    return await services.create_chat(session, payload.title)


@router.get("", response_model=list[ChatResponse])
async def list_chats(session: SessionDep):
    return await services.list_chats(session)


@router.get(
    "/{chat_id}/graph", response_model=ChatGraphResponse,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def get_chat_graph(chat_id: str, session: SessionDep):
    return await services.get_chat_graph(session, chat_id)
