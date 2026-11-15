from fastapi import APIRouter

from src.api.routes import branches, chats, messages

router = APIRouter(prefix="/api/v1")
router.include_router(chats.router)
router.include_router(branches.router)
router.include_router(messages.router)
