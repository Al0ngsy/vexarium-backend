import logging
import stripe

from ..config import settings

logger = logging.getLogger('vexarium.stripe')

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
        from ..api.auth import set_tier
        set_tier(user_id, "pro")
        return {"event": "upgraded", "user_id": user_id, "tier": "pro"}
    if event["type"] == "customer.subscription.deleted":
        # Stripe's subscription object only carries the stripe customer id, not
        # our internal user_id. The customer->user mapping arrives with the
        # Postgres persistence phase, so for now just log and downgrade.
        customer_id = event["data"]["object"].get("customer")
        logger.warning(
            "Stripe subscription deleted for customer %s; user downgrade "
            "requires customer->user mapping (arrives with Postgres phase)",
            customer_id,
        )
        return {"event": "downgraded", "user_id": None, "tier": "free"}
    return {"event": event["type"], "user_id": None, "tier": None}
