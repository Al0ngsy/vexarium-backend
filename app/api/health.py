"""Health-check endpoint."""
from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}