"""Tests for JWT authentication (Task 17).

Auth uses an in-memory user store (Postgres arrives in Task 21). The /me
endpoint takes the token as a query param for simplicity.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services.auth import hash_password, verify_password, create_access_token, decode_token

client = TestClient(app)


def _register(email: str, password: str) -> dict:
    return client.post("/api/v1/auth/register", json={"email": email, "password": password})


def test_register_returns_token():
    resp = _register("alice@example.com", "secret123")
    assert resp.status_code == 201
    body = resp.json()
    assert body["access_token"]
    assert body["tier"] == "free"
    assert body["token_type"] == "bearer"


def test_register_duplicate_email():
    _register("bob@example.com", "secret123")
    resp = _register("bob@example.com", "other123")
    assert resp.status_code == 409


def test_login_success():
    _register("carol@example.com", "secret123")
    resp = client.post(
        "/api/v1/auth/login", json={"email": "carol@example.com", "password": "secret123"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["tier"] == "free"


def test_login_wrong_password():
    _register("dave@example.com", "secret123")
    resp = client.post(
        "/api/v1/auth/login", json={"email": "dave@example.com", "password": "wrongpass"}
    )
    assert resp.status_code == 401


def test_me_returns_user():
    reg = _register("erin@example.com", "secret123")
    token = reg.json()["access_token"]
    resp = client.get(f"/api/v1/auth/me?token={token}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "erin@example.com"
    assert body["tier"] == "free"
    assert isinstance(body["id"], int)


def test_me_invalid_token():
    resp = client.get("/api/v1/auth/me?token=garbage-not-a-real-token")
    assert resp.status_code == 401


def test_hash_verify_password():
    hashed = hash_password("my-pass")
    assert hashed != "my-pass"
    assert verify_password("my-pass", hashed) is True
    assert verify_password("not-my-pass", hashed) is False


def test_create_decode_token():
    token = create_access_token(42, tier="pro")
    payload = decode_token(token)
    assert payload["sub"] == "42"
    assert payload["tier"] == "pro"
