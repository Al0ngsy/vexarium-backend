import logging
import secrets
import time
import uuid

from fastapi import Request

logger = logging.getLogger("vexarium.request")

# Per-request context id, so every log line of one flow can be traced
# together (grep by rid=). Set on the request state in the middleware.
_REQUEST_ID_HEADER = "X-Request-ID"


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "-")


async def request_logging_middleware(request: Request, call_next):
    # Honor an incoming X-Request-ID (load balancers / proxies), else mint one.
    rid = request.headers.get(_REQUEST_ID_HEADER) or f"r{uuid.uuid4().hex[:12]}"
    request.state.request_id = rid
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = (time.perf_counter() - start) * 1000
        logger.exception(
            "rid=%s method=%s path=%s status=500 duration_ms=%.1f UNHANDLED_EXCEPTION",
            rid, request.method, request.url.path, duration_ms,
        )
        raise
    duration_ms = (time.perf_counter() - start) * 1000
    client = request.client.host if request.client else "-"
    logger.info(
        "rid=%s client=%s method=%s path=%s status=%s duration_ms=%.1f",
        rid, client, request.method, request.url.path,
        response.status_code, duration_ms,
    )
    response.headers[_REQUEST_ID_HEADER] = rid
    return response


__all__ = ["request_logging_middleware", "get_request_id", "secrets"]
