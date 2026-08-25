"""The in-app notification feed: one row per bell-icon item.

Deliberately generic and feature-agnostic — any part of the app can create a
row here (``app.notifications.service.create``) without inventing its own
delivery mechanism. The first caller is ``app.profile.notifications``, for
staff visibility/publication overrides; more are expected to follow (the
``category`` field and the loose ``source_type``/``source_id`` pointer exist
for that reason).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import BaseModel
from app.notifications.enums import NotificationCategory


class Notification(BaseModel):
    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    category: Mapped[str] = mapped_column(
        String(30), default=NotificationCategory.GENERAL.value, index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")

    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Loose pointer back to whatever triggered this (e.g. a profile item), so
    # a future "view" action in the dropdown can deep-link. Untyped by design
    # since the source varies per feature — not an FK, the same convention
    # app.profile.models.ProfileVerification uses for the same reason.
    source_type: Mapped[str] = mapped_column(String(30), default="")
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    user = relationship("User", foreign_keys=[user_id])
