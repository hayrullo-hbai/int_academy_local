"""Two-way sync between local social-account claims and hstaff's own profile.

Social accounts no longer go through mentor verification (see
``ReviewableMixin.auto_verify``) — hstaff already tracks ``github_username`` /
``linkedin_username`` / ``discord_username`` on the talent profile, so the
local claim and hstaff's copy are kept as mirrors of one fact instead of two
separate truths: :func:`fetch` pulls the live hstaff value in,
:func:`push` sends a local edit back out.
"""

from __future__ import annotations

import logging

from app.hstaff import client as hstaff
from app.identity.enums import UserSource

logger = logging.getLogger(__name__)

# local platform name -> hstaff profile field
_HSTAFF_FIELDS = {
    "github": "github_username",
    "linkedin": "linkedin_username",
    "discord": "discord_username",
}

# hstaff links Discord via OAuth, which only ever populates the numeric
# `discord_user_id` snowflake — `discord_username` stays empty unless the
# user separately typed a handle into their hstaff profile. Fall back to the
# id so an OAuth-only connection still shows up here. (A bare numeric id is
# also what `validate_account` already accepts for discord — see the
# discord.com/users/{id} URL prefix in services.py.)
_DISCORD_ID_FALLBACK = "discord_user_id"


def _clean(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return ""


def fetch(db, user) -> dict[str, str]:
    """Live hstaff profile fields for the platforms we support, keyed by our
    platform name.

    Uses the service account (same as the public profile bundle) so this
    works regardless of who is viewing, and refreshes the cached
    ``user.hstaff_profile`` mirror on success. Falls back to that cache — or
    nothing — if hstaff is unreachable.
    """
    if user.source != UserSource.HSTAFF.value or not user.hstaff_user_id:
        return {}
    prof = user.hstaff_profile or {}
    try:
        status, body = hstaff.client.get_talent(user.hstaff_user_id)
        if status == 200 and isinstance(body, dict):
            prof = body
            user.hstaff_profile = body
            db.commit()
    except hstaff.HstaffError:
        pass
    out: dict[str, str] = {}
    for platform, field in _HSTAFF_FIELDS.items():
        value = _clean(prof.get(field))
        if not value and platform == "discord":
            value = _clean(prof.get(_DISCORD_ID_FALLBACK))
        if value:
            out[platform] = value
    return out


def _refresh_own_token(db, user) -> bool:
    if not user.hstaff_refresh_token:
        return False
    try:
        data = hstaff.client.refresh(user.hstaff_refresh_token)
    except hstaff.HstaffError:
        return False
    user.hstaff_access_token = data.get("access_token", user.hstaff_access_token)
    user.hstaff_refresh_token = data.get("refresh_token", user.hstaff_refresh_token)
    db.commit()
    return True


def push(db, user, platform: str, username: str) -> None:
    """Best-effort write-through of a local edit to hstaff, under the owner's
    own token so hstaff's RBAC still applies.

    Never raises — the local claim stays the source of truth for our own UI
    even if hstaff is unreachable or the account isn't hstaff-linked.
    """
    field = _HSTAFF_FIELDS.get(platform)
    if (
        not field
        or user.source != UserSource.HSTAFF.value
        or not user.hstaff_access_token
    ):
        return
    try:
        status, _ = hstaff.client.call(
            user.hstaff_access_token, "PUT", "profile/", json={field: username}
        )
        if status == 401 and _refresh_own_token(db, user):
            hstaff.client.call(
                user.hstaff_access_token, "PUT", "profile/", json={field: username}
            )
    except hstaff.HstaffError:
        logger.warning("hstaff social push failed for user %s (%s)", user.id, platform)
