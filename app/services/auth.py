from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import HTTPException, Depends
from fastapi.security import OAuth2PasswordBearer
from ..config import settings
from ..logging import get_logger

logger = get_logger("auth")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

def hash_password(password: str) -> str:
    logger.debug("password hashed")
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    ok = pwd_context.verify(plain, hashed)
    logger.debug("password verify ok=%s", ok)
    return ok

def create_access_token(user_id: int, tier: str = "free") -> str:
    expire = datetime.utcnow() + timedelta(hours=settings.jwt_expiry_hours)
    payload = {"sub": str(user_id), "tier": tier, "exp": expire}
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    logger.debug("access token created user_id=%s tier=%s expires_h=%s", user_id, tier, settings.jwt_expiry_hours)
    return token

def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        logger.debug("token decoded user_id=%s tier=%s", payload.get("sub"), payload.get("tier"))
        return payload
    except JWTError:
        logger.warning("token decode failed (invalid or expired)")
        raise HTTPException(status_code=401, detail="Invalid or expired token")
