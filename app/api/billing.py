import logging

from fastapi import APIRouter, HTTPException, Query, Request

from ..middleware.rate_limit import limiter
from ..services.auth import decode_token
from ..services.stripe_service import create_checkout_session, handle_webhook

router = APIRouter(prefix="/billing", tags=["billing"])

logger = logging.getLogger("vexarium.api.billing")


@router.post("/checkout")
@limiter.limit("60/minute")
async def checkout(request: Request, token: str = Query("")):
    # Require a valid JWT; derive the user id from the token payload (the
    # user_id query param is not trusted).
    payload = decode_token(token)
    user_id = int(payload.get("sub", "0"))
    if user_id < 1:
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        session = create_checkout_session(user_id)
        return {"checkout_url": session.url}
    except Exception:
        logger.error("Checkout failed", exc_info=True)
        raise HTTPException(status_code=500, detail="Checkout failed")


@router.post("/webhook")
@limiter.limit("60/minute")
async def webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        result = handle_webhook(payload, sig_header)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook error: {e}")
