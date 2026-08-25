"""Notifying a profile owner when staff override one of their settings.

In-app only for now (per product decision — email comes later): this writes
into the platform's shared notification feed (``app.notifications``), the
same feed any other bell-icon item comes from, tagged
``NotificationCategory.PROFILE``. When email is ready, it's a second
best-effort call added alongside ``notifications.create`` below — the in-app
write should stay regardless, since it's the one the owner is guaranteed to
see even if outbound email isn't configured in a given deployment.

Like ``profile.mentorship``, a failure here must never block or roll back the
override itself — the override is already committed by the time this runs (see
``services.override_visibility`` / ``services._publication_override``).
"""

import logging

from app.notifications import services as notification_service
from app.notifications.enums import NotificationCategory
from app.profile.enums import PROFILE_SECTION_LABELS

logger = logging.getLogger(__name__)

_TITLES = {
    "visibility": "A staff member changed your item's visibility",
    "publish": "A staff member changed your public profile's publication",
    "sections": "A staff member changed which sections of your profile are public",
    "share": "A staff member changed your profile sharing settings",
}


def _title(field: str) -> str:
    return _TITLES.get(field, "A staff member changed a setting on your profile")


def _change_label(change: dict) -> str:
    field = change["field"]
    if field == "publish":
        return "Publication"
    if field.startswith("sections."):
        section = field.split(".", 1)[1]
        return PROFILE_SECTION_LABELS.get(section, section)
    return field


def _body(
    *,
    actor,
    field: str,
    previous_value: str,
    new_value: str,
    reason: str,
    changes: list | None = None,
) -> str:
    who = actor.full_name or actor.email if actor else "A staff member"
    if changes:
        detail = "; ".join(
            f"{_change_label(c)}: {c['previous'] or '(none)'} → {c['new'] or '(none)'}"
            for c in changes
        )
        text = f"{who} changed your profile sharing: {detail}."
    else:
        label = {
            "visibility": "visibility",
            "publish": "publication",
            "sections": "public sections",
        }.get(field, "setting")
        text = (
            f"{who} changed the {label} from '{previous_value or '(none)'}' "
            f"to '{new_value or '(none)'}'."
        )
    if reason:
        text += f" Reason given: {reason}"
    return text


def notify_owner_of_override(
    db,
    *,
    owner,
    actor,
    item_type: str,
    item_id,
    field: str,
    previous_value: str,
    new_value: str,
    reason: str,
    changes: list | None = None,
) -> None:
    """Best-effort. Never raises — a notification failure is logged, not
    propagated."""
    if owner is None:
        logger.warning("override on %s item but no owner to notify", item_type)
        return
    try:
        notification_service.create(
            db,
            user_id=owner.id,
            category=NotificationCategory.PROFILE.value,
            title=_title(field),
            body=_body(
                actor=actor,
                field=field,
                previous_value=previous_value,
                new_value=new_value,
                reason=reason,
                changes=changes,
            ),
            source_type=item_type,
            source_id=item_id,
        )
    except Exception:  # pragma: no cover - notification is best-effort
        logger.exception(
            "failed to create in-app notification for %s override (owner %s)",
            item_type,
            getattr(owner, "email", owner),
        )
