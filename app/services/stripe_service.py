import stripe

from ..config import settings
from ..logging import get_logger

logger = get_logger("stripe")

stripe.api_key = settings.stripe_secret_key


def create_checkout_session(
    user_id: int,
    tier: str = "pro",
    success_url: str | None = None,
    cancel_url: str | None = None,
):
    # Resolve the Pro price id. A placeholder means Stripe isn't configured yet.
    price_id = settings.stripe_price_id
    if not price_id or price_id == "price_pro_monthly":
        logger.warning("checkout skipped: STRIPE_PRICE_ID not configured (user_id=%s)", user_id)
        raise ValueError("STRIPE_PRICE_ID is not configured. Create a Pro price in the "
                         "Stripe dashboard and set STRIPE_PRICE_ID in the environment.")
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        client_reference_id=str(user_id),
        success_url=success_url or settings.stripe_success_url,
        cancel_url=cancel_url or settings.stripe_cancel_url,
    )
    logger.info("stripe checkout created user_id=%s tier=%s session=%s", user_id, tier, session.id)
    return session


async def handle_webhook(payload: bytes, sig_header: str) -> dict:
    from ..repositories.users import get_user_store

    store = get_user_store()
    event = stripe.Webhook.construct_event(payload, sig_header, settings.stripe_webhook_secret)

    if event["type"] == "checkout.session.completed":
        session = _to_dict(event["data"]["object"])
        user_id = int(session.get("client_reference_id", "0") or 0)
        customer_id = session.get("customer")
        if customer_id and user_id >= 1:
            await store.set_stripe_customer(user_id, customer_id)
        await store.set_tier(user_id, "pro")
        logger.info("stripe webhook event=checkout.session.completed user_id=%s tier=pro", user_id)
        return {"event": "upgraded", "user_id": user_id, "tier": "pro"}

    if event["type"] == "customer.subscription.deleted":
        customer_id = _to_dict(event["data"]["object"]).get("customer")
        downgraded = await _downgrade_by_customer(store, customer_id)
        logger.info("stripe webhook event=customer.subscription.deleted user_id=%s tier=free", downgraded)
        return {"event": "downgraded", "user_id": downgraded, "tier": "free"}

    logger.debug("stripe webhook event=%s (unhandled)", event["type"])
    return {"event": event["type"], "user_id": None, "tier": None}


def _to_dict(obj):
    """Convert a StripeObject (or nested) to plain dicts so .get() works.

    Stripe v15 returns StripeObject instances which do NOT implement .get().
    """
    if hasattr(obj, "to_dict_recursive"):
        return obj.to_dict_recursive()
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return dict(obj)


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
