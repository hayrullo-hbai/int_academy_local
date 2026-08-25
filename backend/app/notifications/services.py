"""Creating and reading in-app notifications.

Small on purpose: ``create`` for any feature that wants to notify a user, and
list/count/mark-read for the bell-icon dropdown to consume. Marking a
notification read removes it from the feed (there is no "read" state to keep).
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.notifications.enums import NOTIFICATION_CATEGORY_VALUES, NotificationCategory
from app.notifications.models import Notification


def create(
    db: Session,
    *,
    user_id,
    title: str,
    body: str = "",
    category: str = NotificationCategory.GENERAL.value,
    source_type: str = "",
    source_id=None,
    commit: bool = True,
) -> Notification:
    """Add one row to ``user_id``'s feed. Unknown categories fall back to
    GENERAL rather than raising, since notification delivery should never be
    the thing that breaks the feature that triggered it."""
    if category not in NOTIFICATION_CATEGORY_VALUES:
        category = NotificationCategory.GENERAL.value
    note = Notification(
        user_id=user_id,
        title=(title or "").strip()[:200],
        body=(body or "").strip(),
        category=category,
        source_type=source_type,
        source_id=source_id,
    )
    db.add(note)
    if commit:
        db.commit()
    return note


def list_for_user(
    db: Session,
    user_id,
    *,
    category: str = "",
    unread_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[Notification]:
    stmt = select(Notification).where(Notification.user_id == user_id)
    if category:
        stmt = stmt.where(Notification.category == category)
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    stmt = stmt.order_by(Notification.created_at.desc())
    stmt = stmt.offset(offset).limit(limit)  # for infinite scroll on UI
    return list(db.execute(stmt).scalars())


# for infinute scroll on notification
def count_for_user(
    db: Session, user_id, *, category: str = "", unread_only: bool = False
) -> int:
    stmt = (
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user_id)
    )
    if category:
        stmt = stmt.where(Notification.category == category)
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    return db.execute(stmt).scalar_one()


def unread_count(db: Session, user_id) -> int:
    return db.execute(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user_id, Notification.is_read.is_(False))
    ).scalar_one()


def mark_read(db: Session, notification: Notification, user) -> Notification:
    if str(notification.user_id) != str(user.id):
        raise PermissionError("This notification doesn't belong to you")
    db.delete(notification)
    db.commit()
    return notification


def mark_all_read(db: Session, user_id) -> int:
    rows = list(
        db.execute(
            select(Notification).where(Notification.user_id == user_id)
        ).scalars()
    )
    for row in rows:
        db.delete(row)
    if rows:
        db.commit()
    return len(rows)
