import stripe

from ..config import settings

stripe.api_key = settings.stripe_secret_key


def create_checkout_session(
    user_id: int,
    tier: str = "pro",
    success_url: str = "http://localhost:5173/pricing?success=1",
    cancel_url: str = "http://localhost:5173/pricing?cancelled=1",
):
    price_id = "price_pro_monthly"  # placeholder — real price ID set in Stripe dashboard
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        client_reference_id=str(user_id),
        success_url=success_url,
        cancel_url=cancel_url,
    )
    return session


def handle_webhook(payload: bytes, sig_header: str) -> dict:
    event = stripe.Webhook.construct_event(payload, sig_header, settings.stripe_webhook_secret)
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        user_id = int(session.get("client_reference_id", "0"))
        return {"event": "upgraded", "user_id": user_id, "tier": "pro"}
    if event["type"] == "customer.subscription.deleted":
        return {"event": "downgraded", "user_id": None, "tier": "free"}
    return {"event": event["type"], "user_id": None, "tier": None}
