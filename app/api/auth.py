from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr
from ..services.auth import hash_password, verify_password, create_access_token, decode_token
from ..models.trade import User
from ..middleware.rate_limit import limiter
from ..config import settings

router = APIRouter(prefix="/auth", tags=["auth"])

# In-memory user store (replaced by Postgres in Task 21)
_users: dict[int, dict] = {}
_next_id = 1

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
    global _next_id
    email = req.email.lower()
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    for u in _users.values():
        if u["email"] == email:
            raise HTTPException(status_code=400, detail="Registration failed")
    user = {"id": _next_id, "email": email, "password_hash": hash_password(req.password), "tier": "free"}
    _users[_next_id] = user
    _next_id += 1
    token = create_access_token(user["id"], user["tier"])
    return TokenResponse(access_token=token, tier=user["tier"])

@router.post("/login", response_model=TokenResponse)
@limiter.limit(f"{settings.rate_limit_free}/minute")
async def login(request: Request, req: LoginRequest):
    email = req.email.lower()
    for u in _users.values():
        if u["email"] == email and verify_password(req.password, u["password_hash"]):
            token = create_access_token(u["id"], u["tier"])
            return TokenResponse(access_token=token, tier=u["tier"])
    raise HTTPException(status_code=401, detail="Invalid credentials")

def set_tier(user_id: int, tier: str):
    user = _users.get(user_id)
    if user:
        user["tier"] = tier


@router.get("/me")
async def me(token: str):
    payload = decode_token(token)
    uid = int(payload["sub"])
    user = _users.get(uid)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    return {"id": user["id"], "email": user["email"], "tier": user["tier"]}
