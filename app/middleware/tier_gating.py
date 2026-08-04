from fastapi import HTTPException

from ..config import settings
from ..repositories.users import get_user_store


async def get_user_tier(token: str = "") -> str:
    # Dev-only bypass: flip DEV_FORCE_PRO=true in .env to preview Pro features locally.
    if settings.dev_force_pro and settings.vexarium_env != "production":
        return "pro"
    if not token:
        return "free"
    from ..services.auth import decode_token

    try:
        payload = decode_token(token)
        uid = int(payload.get("sub", "0"))
        store = get_user_store()
        user = await store.get_by_id(uid)
        return user.get("tier", "free") if user else "free"
    except Exception:
        return "free"


def require_tier(tier: str = "free"):
    async def dependency(token: str = ""):
        user_tier = await get_user_tier(token)
        tiers = {"free": 0, "pro": 1, "enterprise": 2}
        if tiers.get(user_tier, 0) < tiers.get(tier, 0):
            raise HTTPException(
                status_code=403,
                detail=f"Requires {tier} tier. Upgrade to access this feature.",
            )
        return user_tier

    return dependency
