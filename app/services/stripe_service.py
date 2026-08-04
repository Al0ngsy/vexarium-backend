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
    price_id = settings.stripe_price_id or "price_pro_monthly"  # set in Stripe dashboard
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        client_reference_id=str(user_id),
        success_url=success_url,
        cancel_url=cancel_url,
    )
    return session


async def handle_webhook(payload: bytes, sig_header: str) -> dict:
    from ..repositories.users import get_user_store

    store = get_user_store()
    event = stripe.Webhook.construct_event(payload, sig_header, settings.stripe_webhook_secret)

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        user_id = int(session.get("client_reference_id", "0"))
        # Persist the Stripe customer id so we can map future events back to the user.
        customer_id = session.get("customer")
        if customer_id and user_id >= 1:
            await store.set_stripe_customer(user_id, customer_id)
        await store.set_tier(user_id, "pro")
        return {"event": "upgraded", "user_id": user_id, "tier": "pro"}

    if event["type"] == "customer.subscription.deleted":
        # Map the Stripe customer id back to our user (persisted at checkout),
        # then downgrade them to free.
        customer_id = event["data"]["object"].get("customer")
        downgraded = await _downgrade_by_customer(store, customer_id)
        return {"event": "downgraded", "user_id": downgraded, "tier": "free"}

    return {"event": event["type"], "user_id": None, "tier": None}


async def _downgrade_by_customer(store, customer_id: str | None) -> int | None:
    """Downgrade the user that owns ``customer_id`` to free. Returns user id or None."""
    if not customer_id:
        return None
    # Query the store for a user with this stripe_customer_id. For the in-memory
    # store we scan; for Postgres we use a helper. get_user_by_customer is added
    # to both stores.
    user = await store.get_by_stripe_customer(customer_id)
    if user is None:
        logger.warning("No VEXARIUM user mapped to Stripe customer %s", customer_id)
        return None
    await store.set_tier(user["id"], "free")
    return user["id"]
