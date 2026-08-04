"""Tests for Stripe subscription integration (Task 18).

Stripe credentials are empty in the test environment, so the webhook test is
skipped when no webhook secret is configured, and the checkout endpoint is
exercised with a missing key to assert a graceful 500.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.stripe_service import handle_webhook
from app.config import settings

client = TestClient(app)


@pytest.mark.skipif(
    not settings.stripe_webhook_secret,
    reason="stripe_webhook_secret not configured in test env",
)
def test_handle_webhook_missing_secret():
    # A real secret is present, but we pass a bogus/invalid signature so
    # construct_event must raise a StripeError.
    with pytest.raises(Exception):
        handle_webhook(b"{}", "invalid-signature")


def test_checkout_missing_key():
    # No token -> 401 (unauthenticated checkout rejected).
    resp = client.post("/api/v1/billing/checkout", params={"user_id": 1})
    assert resp.status_code == 401


def test_checkout_valid_token_missing_key():
    # Valid token but empty stripe price id -> checkout creation raises -> 500.
    from app.services.auth import create_access_token
    token = create_access_token(1)
    resp = client.post("/api/v1/billing/checkout", params={"token": token})
    assert resp.status_code == 500
    assert resp.json()["detail"] == "Checkout failed"
