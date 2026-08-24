from fastapi import HTTPException

from ..config import settings
from ..logging import get_logger
from ..repositories.users import get_user_store

logger = get_logger("tier")


async def get_user_tier(token: str = "") -> str:
    # Dev-only bypass: flip DEV_FORCE_PRO=true in .env to preview Pro features locally.
    if settings.dev_force_pro and settings.vexarium_env != "production":
        logger.debug("tier resolved user_id=None tier=pro (dev_force_pro)")
        return "pro"
    if not token:
        logger.debug("tier resolved user_id=None tier=free (no token)")
        return "free"
    from ..services.auth import decode_token

    try:
        payload = decode_token(token)
        uid = int(payload.get("sub", "0"))
        store = get_user_store()
        user = await store.get_by_id(uid)
        tier = user.get("tier", "free") if user else "free"
        logger.info("tier resolved user_id=%s tier=%s", uid, tier)
        return tier
    except Exception:
        logger.warning("tier lookup failed (falling back to free)")
        return "free"


def require_tier(tier: str = "free"):
    async def dependency(token: str = ""):
        user_tier = await get_user_tier(token)
        tiers = {"free": 0, "pro": 1, "enterprise": 2}
        allowed = tiers.get(user_tier, 0) >= tiers.get(tier, 0)
        logger.debug("tier gate required=%s got=%s allowed=%s", tier, user_tier, allowed)
        if not allowed:
            raise HTTPException(
                status_code=403,
                detail=f"Requires {tier} tier. Upgrade to access this feature.",
            )
        return user_tier

    return dependency
