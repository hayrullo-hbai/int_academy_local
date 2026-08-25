"""In-app notification feed — the bell-icon dropdown.

Every route here is scoped to the logged-in user's own notifications; there
is no way to read or mark-read anyone else's feed.
"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.responses import error as _err
from app.identity.models import User
from app.notifications import services
from app.notifications.enums import NOTIFICATION_CATEGORY_VALUES
from app.notifications.models import Notification

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _payload(n: Notification) -> dict:
    return {
        "id": str(n.id),
        "category": n.category,
        "title": n.title,
        "body": n.body,
        "is_read": n.is_read,
        "source_type": n.source_type,
        "source_id": str(n.source_id) if n.source_id else None,
        "created_at": n.created_at.isoformat(),
        "read_at": n.read_at.isoformat() if n.read_at else None,
    }


@router.get("")
def list_notifications(
    category: str = Query(default=""),
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if category and category not in NOTIFICATION_CATEGORY_VALUES:
        return _err("Unknown notification category", 400)
    rows = services.list_for_user(
        db,
        user.id,
        category=category,
        unread_only=unread_only,
        limit=limit,
        offset=offset,
    )
    return {
        "total": services.count_for_user(
            db, user.id, category=category, unread_only=unread_only
        ),
        "offset": offset,
        "limit": limit,
        "unread_count": services.unread_count(db, user.id),
        "results": [_payload(n) for n in rows],
    }


@router.get("/unread-count")
def unread_count(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"unread_count": services.unread_count(db, user.id)}


@router.post("/{notification_id}/read")
def mark_read(
    notification_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    note = db.get(Notification, notification_id)
    if not note:
        return _err("Notification not found", 404)
    try:
        services.mark_read(db, note, user)
    except PermissionError as exc:
        return _err(str(exc), 404)
    return {"id": str(note.id), "deleted": True}


@router.post("/read-all")
def mark_all_read(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    count = services.mark_all_read(db, user.id)
    return {"marked_read": count}
