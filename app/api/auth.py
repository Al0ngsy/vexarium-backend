from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr
from ..services.auth import hash_password, verify_password, create_access_token, decode_token
from ..repositories.users import get_user_store
from ..middleware.rate_limit import limiter
from ..config import settings
from ..logging import get_logger
from ..middleware.logging import get_request_id as _rid

router = APIRouter(prefix="/auth", tags=["auth"])
logger = get_logger("auth")

# The user store is Postgres-backed when DATABASE_URL is set, else in-memory.
# Tests run without DATABASE_URL so they stay hermetic. We fetch the live
# singleton on each call so tests can reset it between cases.


def _store():
    return get_user_store()


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    tier: str = "free"


@router.post("/register", response_model=TokenResponse, status_code=201)
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def register(request: Request, req: RegisterRequest):
    email = req.email.lower()
    if len(req.password) < 8:
        logger.info("rid=%s auth register rejected status=400 reason=short_password", _rid(request))
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    existing = await _store().get_by_email(email)
    if existing is not None:
        logger.info("rid=%s auth register rejected status=400 reason=email_taken", _rid(request))
        raise HTTPException(status_code=400, detail="Registration failed")
    user = await _store().create(email, hash_password(req.password), tier="free")
    token = create_access_token(user["id"], user["tier"])
    logger.info("rid=%s auth register done user_id=%s tier=%s", _rid(request), user["id"], user["tier"])
    return TokenResponse(access_token=token, tier=user["tier"])


@router.post("/login", response_model=TokenResponse)
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def login(request: Request, req: LoginRequest):
    email = req.email.lower()
    user = await _store().get_by_email(email)
    if user is None or not verify_password(req.password, user["password_hash"]):
        logger.info("rid=%s auth login rejected status=401", _rid(request))
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token(user["id"], user["tier"])
    logger.info("rid=%s auth login done user_id=%s tier=%s", _rid(request), user["id"], user["tier"])
    return TokenResponse(access_token=token, tier=user["tier"])


@router.get("/me")
async def me(token: str):
    payload = decode_token(token)
    uid = int(payload["sub"])
    user = await _store().get_by_id(uid)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    logger.debug("auth me user_id=%s tier=%s", user["id"], user["tier"])
    return {"id": user["id"], "email": user["email"], "tier": user["tier"]}
