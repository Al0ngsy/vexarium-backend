import logging
import time

from fastapi import Request

logger = logging.getLogger("vexarium.request")


async def request_logging_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "method=%s path=%s status=%s duration_ms=%.1f",
        request.method, request.url.path, response.status_code, duration_ms,
    )
    return response
