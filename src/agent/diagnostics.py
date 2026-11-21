"""Safe container diagnostics for Gemini and its local tools."""

import logging
import json
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator
from time import monotonic

operation_id: ContextVar[str] = ContextVar("gemini_operation_id", default="-")
operation_deadline: ContextVar[float | None] = ContextVar("gemini_operation_deadline", default=None)


@contextmanager
def operation_scope(identifier: str, timeout: float) -> Iterator[None]:
    """Keep the operation identifier and deadline isolated between concurrent tasks."""
    identifier_token = operation_id.set(identifier)
    deadline_token = operation_deadline.set(monotonic() + timeout)
    try:
        yield
    finally:
        operation_id.reset(identifier_token)
        operation_deadline.reset(deadline_token)

import httpx
import httpx2
from google.genai.errors import APIError

logger = logging.getLogger("uvicorn.error.exocortex")

_RPC_STATUSES = frozenset({
    "CANCELLED", "UNKNOWN", "INVALID_ARGUMENT", "DEADLINE_EXCEEDED", "NOT_FOUND",
    "ALREADY_EXISTS", "PERMISSION_DENIED", "RESOURCE_EXHAUSTED", "FAILED_PRECONDITION",
    "ABORTED", "OUT_OF_RANGE", "UNIMPLEMENTED", "INTERNAL", "UNAVAILABLE",
    "DATA_LOSS", "UNAUTHENTICATED",
})


def describe_failure(error: Exception) -> str:
    """Include the full exception detail as a single escaped log field."""
    kind, code, status = "unexpected", "-", "-"
    if isinstance(error, APIError):
        kind = "provider_http"
        code = str(error.code) if isinstance(error.code, int) else "-"
        if isinstance(error.status, str) and error.status in _RPC_STATUSES:
            status = error.status
    elif isinstance(error, (httpx.HTTPStatusError, httpx2.HTTPStatusError)):
        kind, code = "http_response", str(error.response.status_code)
    elif isinstance(error, (TimeoutError, httpx.TimeoutException, httpx2.TimeoutException)):
        kind = "timeout"
    elif isinstance(error, (httpx.ProxyError, httpx2.ProxyError)):
        kind = "proxy"
    elif isinstance(error, (httpx.RequestError, httpx2.RequestError, OSError)):
        kind = "transport"
    detail = json.dumps(str(error), ensure_ascii=False)
    return f"kind={kind} upstream_http={code} upstream_status={status} error_type={type(error).__name__} detail={detail}"


def is_transient_failure(error: Exception) -> bool:
    """Retry provider unavailability, timeouts and connection interruptions."""
    if isinstance(error, APIError):
        return error.code in (503, 504)
    return isinstance(error, (
        TimeoutError, httpx.TimeoutException, httpx2.TimeoutException,
        httpx.ConnectError, httpx2.ConnectError,
    ))
