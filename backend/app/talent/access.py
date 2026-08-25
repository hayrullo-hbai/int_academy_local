"""Who may read / generate / review a talent CV.

Manager means an ``identity.services.MANAGEMENT_ROLES`` holder (5 roles) or the
superuser — exactly what ``identity.services.is_management`` returns. Owner means
the profile owner. Access is otherwise denied.

There is **no default-deny**: every route must call a guard first. The guards are
the boolean ``can_*`` predicates (``can_view_cv`` etc.) and ``_resolve_target``;
routers combine them and return an ``_err`` response when a predicate is false.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.identity.enums import LEARNER_ROLES, SUPERADMIN

# Staff roles that may generate and view learner CVs on the learner's profile page.
CV_STAFF_ROLES = {"mentor", "academy-manager", "hr", SUPERADMIN}

# The section keys a CV can be composed of (used for hidden_sections prefs).
CV_SECTIONS = (
    "summary",
    "contact",
    "skills",
    "projects",
    "languages",
    "experience",
    "certificates",
)


def _is_superuser(user) -> bool:
    """Active superuser OR a granted superadmin role — mirrors ``identity``."""
    return bool(user and (user.is_superuser or user.has_role(SUPERADMIN)))


def _resolve_target(db: Session, email: str):
    """Resolve a ``User`` by ``email`` exactly like ``identity.router.public_profile``:
    exact (case-insensitive) match first, then a ``<ident>@%`` prefix match. Returns
    ``None`` when missing or the matched user is the hidden superadmin.

    Lookups use ``func.lower`` equality / an escaped ``startswith`` so ``%`` / ``_``
    in the path parameter can never act as SQL LIKE wildcards (which would raise
    ``MultipleResultsFound`` → 500), and the whole lookup is defensive: any DB error
    resolves to ``None`` instead of propagating.
    """
    from app.identity.models import User

    ident = (email or "").strip()
    try:
        if not ident:
            return None
        user = db.execute(
            select(User).where(func.lower(User.email) == ident.lower())
        ).scalar_one_or_none()
        if not user and "@" not in ident:
            prefix = (
                ident.lower()
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            user = (
                db.execute(
                    select(User).where(
                        func.lower(User.email).startswith(prefix + "@", escape="\\")
                    )
                )
                .scalars()
                .first()
            )
    except Exception:
        # Any DB hiccup must degrade to "no account", never a 500.
        return None
    if user and _is_superuser(user):
        return None
    return user


def _is_cv_staff(user) -> bool:
    return bool(user and (set(user.role_names) & CV_STAFF_ROLES))


def _is_learner(user) -> bool:
    return bool(user and (set(user.role_names) & LEARNER_ROLES))


def can_view_cv(user, target) -> bool:
    """Owner, management, or CV staff may view CV history / stored CVs / full data."""
    if not user or not target:
        return False
    return user.id == target.id or _is_manager(user) or _is_cv_staff(user)


def can_generate_cv(user, target) -> bool:
    """Learners may generate their own CV; CV staff may generate one for a learner."""
    if not user or not target:
        return False
    if user.id == target.id:
        return _is_learner(user)
    return _is_cv_staff(user) and _is_learner(target)


def can_review(user, target) -> bool:
    """Management-only review capability.

    Reserved for the (deferred) manager approve/reject review UI: data arriving
    ``verified=True`` is currently treated as approved, so this helper gates no
    live endpoint yet but is defined so the review UI can rely on it.
    """
    if not user or not target:
        return False
    return _is_manager(user)


def _is_manager(user) -> bool:
    from app.identity.services import is_management

    return bool(is_management(user))
