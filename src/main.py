from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.agent.loop import BranchBusyError, LLMGenerationError, PromptTooLargeError
from src.api.router import router
from src.schemas import AncestorMutationWarningResponse, RegenerationFailureResponse
from src.services import AncestorMutationWarning, ChatNotFoundError, RegenerationFailedError
from src.storage.crud.graph import (
    BranchAlreadyExistsError,
    BranchConflictError,
    BranchNotFoundError,
    GraphIntegrityError,
    InvalidMergeError,
    MessageNotFoundError,
    NoActiveMessagesToMergeError,
)
from src.storage.database import engine
from src.storage.migrations import require_current_schema


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        await require_current_schema(engine)
        yield
    finally:
        await engine.dispose()


app = FastAPI(title="ExoCortex", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(router)

_ERROR_STATUS: dict[type[Exception], int] = {
    ChatNotFoundError: 404,
    BranchNotFoundError: 404,
    MessageNotFoundError: 404,
    BranchAlreadyExistsError: 409,
    BranchConflictError: 409,
    BranchBusyError: 409,
    InvalidMergeError: 400,
    NoActiveMessagesToMergeError: 400,
    PromptTooLargeError: 413,
    LLMGenerationError: 502,
}


async def domain_error(request: Request, exc: Exception) -> JSONResponse:
    status = next(_ERROR_STATUS[cls] for cls in type(exc).__mro__ if cls in _ERROR_STATUS)
    return JSONResponse(status_code=status, content={"detail": str(exc)})


for error_type in _ERROR_STATUS:
    app.add_exception_handler(error_type, domain_error)


@app.exception_handler(AncestorMutationWarning)
async def ancestor_warning(request: Request, exc: AncestorMutationWarning) -> JSONResponse:
    body = AncestorMutationWarningResponse(affected_branch_count=exc.affected_branch_count, message=str(exc))
    return JSONResponse(status_code=409, content=body.model_dump())


@app.exception_handler(RegenerationFailedError)
async def regeneration_failed(request: Request, exc: RegenerationFailedError) -> JSONResponse:
    body = RegenerationFailureResponse(
        detail=str(exc), branch_id=exc.branch_id, head_message_id=exc.head_message_id,
    )
    status = 413 if isinstance(exc.generation_error, PromptTooLargeError) else 502
    return JSONResponse(status_code=status, content=body.model_dump())


@app.exception_handler(GraphIntegrityError)
async def graph_integrity_error(request: Request, exc: GraphIntegrityError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": "Нарушена целостность графа."})
