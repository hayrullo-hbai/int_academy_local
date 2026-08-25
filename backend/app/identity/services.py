"""Auth + user services.

Login is dual-source:
  * A user row carries `source`. If it's LOCAL, we only ever check the local
    password. If it's HSTAFF, we authenticate by proxying to hstaff.
  * If the email is unknown locally, we try hstaff /auth/login (public). On
    success we create a mirrored local row flagged source=HSTAFF.
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    REFRESH_TOKEN_EXPIRE_HOURS,
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.hstaff import client as hstaff
from app.identity.enums import (
    SUPERADMIN,
    OFFICE_LOCATION_VALUES,
    UserSource,
    UserStatus,
)
from app.identity.rbac_catalog import ROLES
from app.identity.models import (
    MenteeRecord,
    PasswordResetToken,
    Permission,
    RefreshToken,
    Role,
    User,
    user_roles,
)

logger = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc)


# ---------- token helpers ----------
def _issue_refresh_token(db: Session, user_id) -> str:
    raw = generate_refresh_token()
    db.add(
        RefreshToken(
            user_id=user_id,
            token_hash=hash_token(raw),
            expires_at=_now() + timedelta(hours=REFRESH_TOKEN_EXPIRE_HOURS),
        )
    )
    db.flush()
    return raw


def _session(db: Session, user: User) -> dict:
    """Build our own token pair for a user (regardless of their source)."""
    result = {
        "access_token": create_access_token(
            {"sub": str(user.id), "role": user.role_name}
        ),
        "refresh_token": _issue_refresh_token(db, user.id),
        "token_type": "bearer",
        "source": user.source,
        "must_change_password": user.must_change_password,
    }
    db.commit()
    return result


def _is_superadmin(user: User) -> bool:
    return bool(user.is_superuser or user.has_role(SUPERADMIN))


# ---------- login (hstaff mirror) ----------
def _upsert_role(db: Session, name: str, catalog: dict[str, dict]) -> Role:
    """Ensure a local Role row exists for an hstaff role, keeping metadata fresh."""
    meta = catalog.get(name, {})
    role = db.execute(select(Role).where(Role.name == name)).scalar_one_or_none()
    if role is None:
        role = Role(name=name, is_system=True)
        db.add(role)
        db.flush()
    for field, key in (
        ("display_name", "display_name"),
        ("description", "description"),
    ):
        val = meta.get(key)
        if val and getattr(role, field) != val:
            setattr(role, field, val)
    if meta.get("level") is not None and role.level != meta["level"]:
        role.level = meta["level"]
    if not role.is_system:
        role.is_system = True
    return role


def _mirror_hstaff_user(db: Session, email: str, hstaff_payload: dict) -> User:
    """Create/refresh a local mirror for an hstaff-authenticated user."""
    from app.onboarding.models import OnboardingPipeline

    info = hstaff_payload.get("user") or {}
    access = hstaff_payload.get("access_token") or ""
    refresh = hstaff_payload.get("refresh_token") or ""
    full_name = " ".join(
        p for p in [info.get("first_name"), info.get("last_name")] if p
    ).strip()

    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        user = User(
            email=email,
            source=UserSource.HSTAFF.value,
            is_verified=True,
            status=UserStatus.ACTIVE.value,
        )
        db.add(user)
        db.flush()

    user.source = UserSource.HSTAFF.value
    user.is_verified = True
    if info.get("id") is not None:
        user.hstaff_user_id = info["id"]
    if full_name and not user.name_customized:  # a locally-edited name wins
        user.full_name = full_name
    if info.get("img_url"):
        user.img_url = info["img_url"]
    user.hstaff_access_token = access
    user.hstaff_refresh_token = refresh
    user.password_hash = None  # hstaff owns the password

    primary = (info.get("type") or "").strip()
    role_names = [primary] if primary else []
    try:
        perms = hstaff.client.get_my_permissions(access)
        user.cached_permissions = perms.get("permissions", [])
        role_names = perms.get("roles") or role_names
        primary = perms.get("primary_role") or primary
        catalog = {r["name"]: r for r in perms.get("available_roles", [])}
    except hstaff.HstaffError:
        catalog = {}
    try:
        profile = hstaff.client.get_profile(access)
        # hstaff's /profile/ returns the user id as `id` and no `talent_id`, but
        # the two are the same number (talent rows carry talent_id == profile id).
        # Persist it as `talent_id` so talent-scoped views can rely on the field.
        if isinstance(profile, dict) and profile.get("id") is not None:
            profile.setdefault("talent_id", profile["id"])
        user.hstaff_profile = profile
    except hstaff.HstaffError:
        pass

    roles = [_upsert_role(db, n, catalog) for n in role_names if n]
    if roles:
        primary_role = next((r for r in roles if r.name == primary), roles[0])
        user.role = primary_role
        user.roles = roles
    db.flush()

    # First hstaff login: adopt any userless onboarding pipeline tracking them.
    for p in db.execute(
        select(OnboardingPipeline).where(
            OnboardingPipeline.user_id.is_(None),
            OnboardingPipeline.candidate_email.ilike(email),
        )
    ).scalars():
        p.user_id = user.id
    db.flush()
    return user


def sync_mentor_role(db: Session, user: User) -> None:
    """Auto-grant the local ``mentor`` role to anyone hstaff says mentors.

    Runs at login for every hstaff user, after their roles have been mirrored.
    hstaff owns the mentor↔mentee relationship, so we always ask it
    (``HSTAFF_MENTEES_PATH``) and reconcile against the answer:

    * mentees reported → mirror them into ``MenteeRecord`` (adding new ones,
      dropping ones hstaff no longer lists) and, if the user isn't already a
      mentor, add the role as a *regular* role — ``user.role`` (the primary /
      active role) is never touched.
    * hstaff says "no mentees" → drop the mirrored rows. We don't strip the
      mentor role: ``_mirror_hstaff_user`` already rewrote ``user.roles`` from
      hstaff this login, so a role we auto-granted before is gone unless hstaff
      itself grants it.
    * hstaff couldn't answer (``None``) → leave the mirror alone and re-grant
      the role if we still have mentees on file, so a degraded hstaff doesn't
      silently demote a mentor.

    Never raises: any hstaff failure leaves the local mirror unchanged, so a
    degraded hstaff can never block a login.
    """
    if user.source != UserSource.HSTAFF.value or not user.hstaff_user_id:
        return  # local-only accounts have no hstaff mentorship record to consult

    from app.profile.mentorship import lookup_mentees

    # hstaff's /profile/ carries a `has_mentees` flag, already fetched above with
    # the user's *own* token. It answers "is this person a mentor?" without the
    # service account or HSTAFF_MENTEES_PATH, so the role can be granted even
    # while those are unavailable. It says nothing about *who* the mentees are —
    # the mirror below still needs the mentees route.
    if _hstaff_says_has_mentees(user) and _grant_mentor_role(db, user):
        logger.info("Granted mentor role to %s (hstaff has_mentees)", user.email)

    try:
        emails = lookup_mentees(db, user)
    except Exception as exc:  # pragma: no cover - defensive; lookup never raises
        logger.warning("hstaff mentees check for %s failed: %s", user.email, exc)
        emails = None

    mirrored = {
        row[0]: row[1]
        for row in db.execute(
            select(MenteeRecord.mentee_email, MenteeRecord.id).where(
                MenteeRecord.mentor_id == user.id
            )
        )
    }

    if emails is None:
        # Unknown — keep what hstaff last told us, and keep the role with it.
        if mirrored:
            _grant_mentor_role(db, user)
        return

    for email in emails:
        if email and email not in mirrored:
            db.add(MenteeRecord(mentor_id=user.id, mentee_email=email))
    stale = [rid for email, rid in mirrored.items() if email not in emails]
    if stale:
        db.execute(delete(MenteeRecord).where(MenteeRecord.id.in_(stale)))

    if emails:
        granted = _grant_mentor_role(db, user)
        if granted:
            logger.info(
                "Auto-granted mentor role to %s (%d mentees mirrored)",
                user.email,
                len(emails),
            )
    db.flush()


def _hstaff_says_has_mentees(user: User) -> bool:
    """Whether hstaff's cached profile flags this user as mentoring someone.

    Only an explicit truthy `has_mentees` counts. A missing key means the hstaff
    profile fetch failed or that deployment doesn't send the flag — which is
    "unknown", not "no", so we leave the role decision to the mentees lookup
    rather than acting on an absence.
    """
    profile = user.hstaff_profile
    if not isinstance(profile, dict):
        return False
    return profile.get("has_mentees") is True


def _grant_mentor_role(db: Session, user: User) -> bool:
    """Add ``mentor`` as a regular (non-primary) role. True if it was added."""
    if user.has_role("mentor"):
        return False
    role = _upsert_role(db, "mentor", ROLES)
    if role not in user.roles:
        user.roles = list(user.roles) + [role]
    db.flush()
    return True


def login_user(db: Session, email, password) -> dict | None:
    email = (email or "").strip().lower()
    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()

    # Local users (superadmin, dev) always authenticate locally — bypass hstaff.
    if existing and existing.source == UserSource.LOCAL.value:
        if not verify_password(password, existing.password_hash):
            return None
        return _session(db, existing)

    # Unknown email or hstaff user → try hstaff (public endpoint).
    if settings.HSTAFF_ENABLED:
        try:
            payload = hstaff.client.login(email, password)
        except hstaff.HstaffAuthError as exc:
            logger.warning("hstaff login rejected for %s: %s", email, exc)
            return None
        except hstaff.HstaffError as exc:
            # Network / 5xx — indistinguishable from bad creds at the UI, so log it.
            logger.error("hstaff login unreachable for %s: %s", email, exc)
            return None
        user = _mirror_hstaff_user(db, email, payload)
        sync_mentor_role(db, user)
        return _session(db, user)
    return None


# ---------- refresh (single-use rotation) ----------
def refresh_token(db: Session, raw: str) -> dict | None:
    if not raw:
        return None
    tok = db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_token(raw),
            RefreshToken.revoked_at.is_(None),
        )
    ).scalar_one_or_none()
    if not tok:
        return None
    if tok.expires_at < _now():
        raise ValueError("Refresh token expired")
    tok.revoked_at = _now()
    db.flush()
    return _session(db, db.get(User, tok.user_id))


def logout_user(db: Session, raw: str) -> bool:
    if not raw:
        return False
    tok = db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_token(raw),
            RefreshToken.revoked_at.is_(None),
        )
    ).scalar_one_or_none()
    if not tok:
        return False
    tok.revoked_at = _now()
    db.commit()
    return True


# ---------- role assignment ----------
def set_active_role(db: Session, user: User, role_name: str) -> User:
    if role_name not in user.role_names:
        raise ValueError("You don't hold that role")
    user.role = db.execute(select(Role).where(Role.name == role_name)).scalar_one()
    db.commit()
    return user


# ---------- hstaff bulk sync ----------
def sync_hstaff_users(db: Session) -> dict:
    """Pull all users from hstaff and create local mirror rows for new ones.

    Uses the privileged service account to fetch hstaff's user list, then skips
    emails already present in the local ``users`` table.  New users are created
    with ``source=HSTAFF`` and their basic info (name, role, etc.) but without
    hstaff tokens — they'll get those on their first login.

    Returns a summary dict with ``created`` (list of emails) and ``count``.
    """
    status, data = hstaff.client.list_users()
    if status != 200 or not isinstance(data, list):
        raise RuntimeError(f"Failed to list hstaff users (status={status}).")

    existing_emails: set[str] = {row[0] for row in db.execute(select(User.email)).all()}

    created: list[str] = []
    for item in data:
        email = (item.get("email") or "").strip().lower()
        if not email or email in existing_emails:
            continue

        first = (item.get("first_name") or "").strip()
        last = (item.get("last_name") or "").strip()
        full_name = " ".join(p for p in [first, last] if p).strip() or None

        user = User(
            email=email,
            full_name=full_name,
            source=UserSource.HSTAFF.value,
            status=UserStatus.ACTIVE.value,
            is_active=True,
            is_verified=bool(item.get("is_verified", False)),
            hstaff_user_id=item.get("id"),
            img_url=item.get("img_url") or None,
        )

        primary_role_name = (item.get("type") or "").strip()
        if primary_role_name:
            role = db.execute(
                select(Role).where(Role.name == primary_role_name)
            ).scalar_one_or_none()
            if role:
                user.role = role
                user.roles = [role]

        db.add(user)
        db.flush()
        created.append(email)
        existing_emails.add(email)

    db.commit()
    return {"created": created, "count": len(created)}


# ---------- admin: user management ----------
MANAGEMENT_ROLES = {"academy-manager", "hr", "department-head", "c-level", SUPERADMIN}
ELEVATED_ROLES = {"superadmin", "c-level", "advisor", "cfo"}
EXECUTIVE_ROLES = {"c-level"}
EXEC_ASSIGNER_ROLES = {"c-level"}


def _roles(user) -> set[str]:
    return set(user.role_names) | ({user.role_name} if user.role_name else set())


def _can_assign_executive(user) -> bool:
    if not user:
        return False
    return bool(user.is_superuser or (_roles(user) & EXEC_ASSIGNER_ROLES))


def is_management(user) -> bool:
    if not user:
        return False
    return bool(user.is_superuser or (_roles(user) & MANAGEMENT_ROLES))


# Only the superadmin and academy manager may create/edit/delete users; everyone
# else can only view the user list.
USER_ADMIN_ROLES = {SUPERADMIN, "academy-manager"}


def is_user_admin(user) -> bool:
    if not user:
        return False
    return bool(user.is_superuser or (_roles(user) & USER_ADMIN_ROLES))


def _is_elevated(user) -> bool:
    if not user:
        return False
    return bool(user.is_superuser or (_roles(user) & ELEVATED_ROLES))


def create_user(
    db: Session,
    *,
    email,
    full_name,
    password,
    phone=None,
    office_location=None,
    role_names=None,
    requester,
) -> User:
    email = (email or "").strip().lower()
    if not email:
        raise ValueError("Email is required")
    if not password:
        raise ValueError("A password is required")
    if db.execute(select(User).where(User.email == email)).scalar_one_or_none():
        raise ValueError("A user with that email already exists")

    names = [r for r in (role_names or []) if r]
    if SUPERADMIN in names:
        raise ValueError("Assigning the superadmin role is not permitted")
    if (set(names) & EXECUTIVE_ROLES) and not _can_assign_executive(requester):
        raise ValueError("Only the superadmin or a C-level can assign executive roles")
    if (set(names) & ELEVATED_ROLES) and not _is_elevated(requester):
        raise ValueError("Only a C-level can assign executive roles")

    roles = list(db.execute(select(Role).where(Role.name.in_(names))).scalars())
    missing = set(names) - {r.name for r in roles}
    if missing:
        raise ValueError(f"Unknown role(s): {', '.join(sorted(missing))}")

    user = User(
        email=email,
        full_name=(full_name or "").strip() or None,
        source=UserSource.LOCAL.value,
        status=UserStatus.ACTIVE.value,
        is_active=True,
        is_verified=True,
        must_change_password=True,
        password_hash=hash_password(password),
    )
    phone = (phone or "").strip()
    if phone:
        user.phone = phone
    if office_location and office_location in OFFICE_LOCATION_VALUES:
        user.office_location = office_location
    db.add(user)
    db.flush()
    if roles:
        user.roles = roles
        user.role = roles[0]
    db.commit()
    return user


def set_user_roles(
    db: Session, target: User, role_names, primary_name, requester
) -> User:
    names = [r for r in (role_names or []) if r]

    if _is_superadmin(target) and not _is_superadmin(requester):
        raise ValueError("You can't modify a superadmin")
    if SUPERADMIN in names and not _is_superadmin(target):
        raise ValueError("Assigning the superadmin role is not permitted")
    target_exec = set(target.role_names) & EXECUTIVE_ROLES
    if ((set(names) & EXECUTIVE_ROLES) ^ target_exec) and not _can_assign_executive(
        requester
    ):
        raise ValueError("Only the superadmin or a C-level can change executive roles")
    if _is_elevated(target) and not _is_elevated(requester):
        raise ValueError("Only a C-level can modify an executive account")
    if (set(names) & ELEVATED_ROLES) and not _is_elevated(requester):
        raise ValueError("Only a C-level can assign executive roles")

    roles = list(db.execute(select(Role).where(Role.name.in_(names))).scalars())
    found = {r.name for r in roles}
    missing = set(names) - found
    if missing:
        raise ValueError(f"Unknown role(s): {', '.join(sorted(missing))}")

    target.roles = roles
    if roles:
        primary = primary_name if primary_name in found else names[0]
        target.role = next(r for r in roles if r.name == primary)
    else:
        target.role = None
    db.commit()
    return target


def set_user_active(db: Session, target: User, is_active: bool, requester) -> User:
    if _is_superadmin(target) and not _is_superadmin(requester):
        raise ValueError("You can't modify a superadmin")
    if _is_elevated(target) and not _is_elevated(requester):
        raise ValueError("Only a C-level can modify an executive account")
    target.is_active = bool(is_active)
    if not target.is_active:  # kill live sessions on deactivation
        for tok in db.execute(
            select(RefreshToken).where(
                RefreshToken.user_id == target.id, RefreshToken.revoked_at.is_(None)
            )
        ).scalars():
            tok.revoked_at = _now()
    db.commit()
    return target


def delete_user(db: Session, target: User, requester) -> None:
    if target.id == requester.id:
        raise ValueError("You can't delete your own account")
    if _is_superadmin(target) and not _is_superadmin(requester):
        raise ValueError("You can't delete a superadmin")
    if _is_elevated(target) and not _is_elevated(requester):
        raise ValueError("Only a C-level can delete an executive account")
    db.delete(target)
    db.commit()


# ---------- role CRUD (local / management) ----------
def create_role(
    db: Session,
    *,
    name,
    display_name,
    description,
    level,
    permission_codenames,
    requester,
) -> Role:
    name = (name or "").strip().lower().replace(" ", "-")
    if not name:
        raise ValueError("Role name is required")
    if not display_name:
        raise ValueError("Display name is required")
    if db.execute(select(Role).where(Role.name == name)).scalar_one_or_none():
        raise ValueError(f"A role named '{name}' already exists")
    if SUPERADMIN in (name,):
        raise ValueError("Cannot create a superadmin role")
    if not _is_elevated(requester):
        raise ValueError("Only a C-level can create roles")
    perms = list(
        db.execute(
            select(Permission).where(
                Permission.codename.in_(permission_codenames or [])
            )
        ).scalars()
    )
    role = Role(
        name=name,
        display_name=display_name,
        description=description,
        level=level,
        is_system=False,
        created_by_id=requester.id,
    )
    db.add(role)
    db.flush()
    role.permissions = perms
    db.commit()
    return role


def update_role(
    db: Session,
    role_id,
    *,
    display_name,
    description,
    level,
    permission_codenames,
    requester,
) -> Role:
    role = db.get(Role, role_id)
    if not role:
        raise ValueError("Role not found")
    if role.name == SUPERADMIN:
        raise ValueError("Cannot modify the superadmin role")
    if role.is_system and not _is_elevated(requester):
        raise ValueError("Only a C-level can modify a system role")
    if not _is_elevated(requester):
        raise ValueError("Only a C-level can update roles")
    if display_name is not None:
        role.display_name = display_name
    if description is not None:
        role.description = description
    if level is not None:
        role.level = level
    if permission_codenames is not None:
        perms = list(
            db.execute(
                select(Permission).where(Permission.codename.in_(permission_codenames))
            ).scalars()
        )
        role.permissions = perms
    db.commit()
    return role


def delete_role(db: Session, role_id, requester) -> None:
    role = db.get(Role, role_id)
    if not role:
        raise ValueError("Role not found")
    if role.name == SUPERADMIN:
        raise ValueError("Cannot delete the superadmin role")
    if role.is_system:
        raise ValueError("Cannot delete a system role")
    if not _is_elevated(requester):
        raise ValueError("Only a C-level can delete roles")
    # Detach users
    for u in db.execute(select(User).where(User.role_id == role.id)).scalars():
        u.role_id = None
        u.role = None
    db.execute(user_roles.delete().where(user_roles.c.role_id == role.id))
    role.permissions = []
    db.flush()
    db.delete(role)
    db.commit()


# ---------- forgot / reset password (local users only) ----------
def request_password_reset(db: Session, email: str) -> None:
    email = (email or "").strip().lower()
    user = db.execute(
        select(User).where(
            User.email == email,
            User.is_active.is_(True),
            User.source == UserSource.LOCAL.value,
        )
    ).scalar_one_or_none()
    if not user:
        return  # don't reveal existence
    raw = secrets.token_urlsafe(48)
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_token(raw),
            expires_at=_now() + timedelta(hours=1),
        )
    )
    db.commit()
    link = f"{settings.FRONTEND_RESET_URL}?token={raw}"
    # Console email backend equivalent: log the reset link.
    logger.info("Password reset for %s (valid 1 hour): %s", user.email, link)


def confirm_password_reset(db: Session, raw_token: str, new_password: str) -> None:
    tok = db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == hash_token(raw_token or ""),
            PasswordResetToken.used_at.is_(None),
        )
    ).scalar_one_or_none()
    if not tok:
        raise ValueError("Invalid or expired reset token")
    if tok.expires_at < _now():
        raise ValueError("Reset token expired")
    user = db.get(User, tok.user_id)
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    tok.used_at = _now()
    for rt in db.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)
        )
    ).scalars():
        rt.revoked_at = _now()
    db.commit()
