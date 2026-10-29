import secrets
from datetime import datetime, timedelta

import jwt  # PyJWT (python-jose is unmaintained and has an unfixed JWT CVE)
from passlib.context import CryptContext

from .config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------- Password hashing ----------
def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


# ---------- JWT access tokens ----------
def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> dict:
    # `algorithms` is pinned (never taken from the token header) and exp/sub are
    # REQUIRED, so a token without an expiry is rejected instead of living forever.
    return jwt.decode(
        token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM],
        options={"require": ["exp", "sub"]},
    )


# ---------- Refresh tokens ----------
# Refresh tokens are random opaque strings (not JWTs), stored in the DB
# (RefreshToken table) - this makes logout / revocation simple
# (just set the DB row's revoked=True).
def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def refresh_token_expiry() -> datetime:
    return datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)


# ---------- Password reset / email verification tokens ----------
def generate_secure_token() -> str:
    return secrets.token_urlsafe(32)


def reset_token_expiry() -> datetime:
    return datetime.utcnow() + timedelta(minutes=settings.RESET_TOKEN_EXPIRE_MINUTES)


def verify_token_expiry() -> datetime:
    return datetime.utcnow() + timedelta(hours=settings.VERIFY_TOKEN_EXPIRE_HOURS)
