"""Auth dependency.

Validates our own access tokens (issued for both local and hstaff users). The
token's `sub` is our local User UUID regardless of source, so downstream
permission checks always read roles from our mirrored user row.

Per-endpoint authorization is done inline in the routers (via the small role
helpers in identity.services / onboarding.access), so there are no permission
dependency factories here.
"""

import uuid

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token
from app.identity.models import User

_UNAUTH = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization:
        raise _UNAUTH
    parts = authorization.split()
    if len(parts) != 2 or parts[0] != "Bearer":
        raise _UNAUTH
    token = parts[1]
    try:
        payload = decode_access_token(token)
        user_id = payload.get("sub")
        if user_id is None:
            raise _UNAUTH
        uid = uuid.UUID(user_id)
    except (jwt.PyJWTError, ValueError):
        raise _UNAUTH

    user = db.get(User, uid)
    if not user or not user.is_active:
        raise _UNAUTH
    return user


def get_optional_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User | None:
    """Like get_current_user but returns None instead of 401 when there is no
    valid token. Used by publicly shareable read endpoints so anonymous
    visitors can view them while logged-in users still get their identity."""
    if not authorization:
        return None
    try:
        return get_current_user(authorization=authorization, db=db)
    except HTTPException:
        return None
