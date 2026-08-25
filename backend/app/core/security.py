import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext

from app.core.config import settings

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_HOURS = 24 * 7

# `django_pbkdf2_sha256` is included so password hashes migrated from the Django
# backend (format: "pbkdf2_sha256$<iter>$<salt>$<hash>") verify unchanged. New
# hashes are written with passlib's own pbkdf2_sha256.
_pwd = CryptContext(
    schemes=["pbkdf2_sha256", "django_pbkdf2_sha256"],
    default="pbkdf2_sha256",
    deprecated="auto",
)


# ---- password hashing (local users) ----
def hash_password(raw: str) -> str:
    return _pwd.hash(raw)


def verify_password(raw: str, hashed: str | None) -> bool:
    """False for hstaff users (unusable/None password) or an empty candidate."""
    if not hashed or not raw:
        return False
    try:
        return _pwd.verify(raw, hashed)
    except ValueError:
        return False


# ---- JWT access tokens ----
def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode["exp"] = expire
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])


# ---- opaque tokens (refresh / reset) ----
def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(raw: str) -> str:
    """SHA-256 for opaque tokens (refresh / reset). Fast, unique-indexable."""
    return hashlib.sha256(raw.encode()).hexdigest()
