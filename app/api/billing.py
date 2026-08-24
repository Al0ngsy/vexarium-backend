from fastapi import APIRouter, HTTPException, Query, Request

from ..middleware.rate_limit import limiter
from ..services.auth import decode_token
from ..services.stripe_service import create_checkout_session, handle_webhook
from ..logging import get_logger
from ..middleware.logging import get_request_id as _rid

router = APIRouter(prefix="/billing", tags=["billing"])

logger = get_logger("billing")


@router.post("/checkout")
@limiter.limit("60/minute")
async def checkout(request: Request, token: str = Query("")):
    # Require a valid JWT; derive the user id from the token payload (the
    # user_id query param is not trusted).
    payload = decode_token(token)
    user_id = int(payload.get("sub", "0"))
    if user_id < 1:
        logger.info("rid=%s billing checkout rejected status=401", _rid(request))
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        session = create_checkout_session(user_id)
        logger.info("rid=%s billing checkout done user_id=%d session_id=%s", _rid(request), user_id, session.id)
        return {"checkout_url": session.url}
    except Exception:
        logger.error("rid=%s billing checkout user_id=%d FAILED", _rid(request), user_id, exc_info=True)
        raise HTTPException(status_code=500, detail="Checkout failed")


@router.post("/webhook")
@limiter.limit("60/minute")
async def webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        result = await handle_webhook(payload, sig_header)
        logger.info(
            "rid=%s billing webhook done event=%s user_id=%s tier=%s",
            _rid(request), result.get("event"), result.get("user_id"), result.get("tier"),
        )
        return result
    except Exception as e:
        logger.warning("rid=%s billing webhook rejected status=400 err=%s", _rid(request), e)
        raise HTTPException(status_code=400, detail=f"Webhook error: {e}")
