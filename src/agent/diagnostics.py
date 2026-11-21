"""Safe container diagnostics for Gemini and its local tools."""

import logging
import json
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
