from fastapi import APIRouter, HTTPException, Request

from ..services.stripe_service import create_checkout_session, handle_webhook

router = APIRouter(prefix="/billing", tags=["billing"])


@router.post("/checkout")
async def checkout(user_id: int):
    try:
        session = create_checkout_session(user_id)
        return {"checkout_url": session.url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stripe checkout failed: {e}")


@router.post("/webhook")
async def webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        result = handle_webhook(payload, sig_header)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Webhook error: {e}")
