"""User persistence.

Postgres-backed when ``DATABASE_URL`` is set, in-memory otherwise (so tests and
an unconfigured local run stay hermetic). The in-memory store is also mirrored
to keep the same behavior the codebase already relies on.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session_factory, db_enabled
from ..models.trade import User

logger = logging.getLogger("vexarium.users")


class UserStore:
    """Single seam for user CRUD + tier management."""

    async def create(self, email: str, password_hash: str, tier: str = "free",
                     stripe_customer_id: Optional[str] = None) -> dict:
        raise NotImplementedError

    async def get_by_email(self, email: str) -> Optional[dict]:
        raise NotImplementedError

    async def get_by_id(self, user_id: int) -> Optional[dict]:
        raise NotImplementedError

    async def set_tier(self, user_id: int, tier: str) -> None:
        raise NotImplementedError

    async def set_stripe_customer(self, user_id: int, customer_id: str) -> None:
        raise NotImplementedError

    async def get_by_stripe_customer(self, customer_id: str) -> Optional[dict]:
        raise NotImplementedError


def _to_dict(u: User) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "password_hash": u.password_hash,
        "tier": u.tier,
        "stripe_customer_id": u.stripe_customer_id,
        "created_at": u.created_at,
    }


class PostgresUserStore(UserStore):
    def __init__(self, session_factory):
        self._sf = session_factory

    async def create(self, email, password_hash, tier="free", stripe_customer_id=None) -> dict:
        async with self._sf() as s:  # type: AsyncSession
            u = User(email=email, password_hash=password_hash, tier=tier,
                     stripe_customer_id=stripe_customer_id)
            s.add(u)
            await s.commit()
            await s.refresh(u)
            return _to_dict(u)

    async def get_by_email(self, email) -> Optional[dict]:
        async with self._sf() as s:
            res = await s.execute(select(User).where(User.email == email))
            u = res.scalar_one_or_none()
            return _to_dict(u) if u else None

    async def get_by_id(self, user_id) -> Optional[dict]:
        async with self._sf() as s:
            u = await s.get(User, user_id)
            return _to_dict(u) if u else None

    async def set_tier(self, user_id, tier) -> None:
        async with self._sf() as s:
            u = await s.get(User, user_id)
            if u:
                u.tier = tier
                await s.commit()

    async def set_stripe_customer(self, user_id, customer_id) -> None:
        async with self._sf() as s:
            u = await s.get(User, user_id)
            if u:
                u.stripe_customer_id = customer_id
                await s.commit()

    async def get_by_stripe_customer(self, customer_id) -> Optional[dict]:
        async with self._sf() as s:
            res = await s.execute(select(User).where(User.stripe_customer_id == customer_id))
            u = res.scalar_one_or_none()
            return _to_dict(u) if u else None


class InMemoryUserStore(UserStore):
    def __init__(self):
        self._users: dict[int, dict] = {}
        self._by_email: dict[str, dict] = {}
        self._next_id = 1

    async def create(self, email, password_hash, tier="free", stripe_customer_id=None) -> dict:
        for u in self._users.values():
            if u["email"] == email:
                raise ValueError("Email already registered")
        u = {"id": self._next_id, "email": email, "password_hash": password_hash,
             "tier": tier, "stripe_customer_id": stripe_customer_id,
             "created_at": None}
        self._users[self._next_id] = u
        self._by_email[email] = u
        self._next_id += 1
        return u

    async def get_by_email(self, email) -> Optional[dict]:
        return self._by_email.get(email)

    async def get_by_id(self, user_id) -> Optional[dict]:
        return self._users.get(user_id)

    async def set_tier(self, user_id, tier) -> None:
        u = self._users.get(user_id)
        if u:
            u["tier"] = tier

    async def set_stripe_customer(self, user_id, customer_id) -> None:
        u = self._users.get(user_id)
        if u:
            u["stripe_customer_id"] = customer_id

    async def get_by_stripe_customer(self, customer_id) -> Optional[dict]:
        for u in self._users.values():
            if u.get("stripe_customer_id") == customer_id:
                return u
        return None


_singleton: UserStore | None = None


def get_user_store() -> UserStore:
    """Return a cached singleton store (Postgres if configured, else in-memory).

    Cached so the same store instance is shared across auth, tier-gating, trades,
    and billing within a process. Without caching, a freshly-created in-memory
    store would lose users between calls.
    """
    global _singleton
    if _singleton is not None:
        return _singleton
    if db_enabled():
        sf = get_session_factory()
        if sf is not None:
            _singleton = PostgresUserStore(sf)
            return _singleton
    _singleton = InMemoryUserStore()
    return _singleton


def reset_user_store() -> None:
    """Drop the singleton (used by tests to reset state)."""
    global _singleton
    _singleton = None
