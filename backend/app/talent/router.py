"""Talent CV routes (``prefix="/talent"``).

Every route authorizes FIRST (no default-deny): it resolves the target user,
then calls an access guard before doing any work. Failures are returned via
``_err(detail, status)`` — never raised.
"""

import uuid

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.responses import error as _err
from app.identity.models import User
from app.profile.models import ProfileShare
from app.talent import access, services
from app.talent.models import TalentProject

router = APIRouter(prefix="/talent")


# ---------- guards ----------
def _require_target(db: Session, email: str):
    """Resolve + authorize helpers. Returns (err_or_None, target)."""
    target = access._resolve_target(db, email)
    if not target:
        return _err("User not found", 404), None
    return None, target


def _require_owner_or_manager(user, target):
    if not access.can_view_cv(user, target):
        return _err("Only the profile owner or management may do that", 403)
    return None


def _require_owner(user, target):
    if not user or user.id != target.id:
        return _err("Only the profile owner may do that", 403)
    return None


def _require_owner_or_cv_staff(user, target):
    if not access.can_generate_cv(user, target):
        return _err(
            "Only the profile owner or CV staff may generate a CV for a learner", 403
        )
    return None


# ---------- CV generation ----------
@router.post("/{email}/cv/generate")
def generate_cv(
    email: str,
    body: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Generate a CV for the target user.

    ``source`` selects the data source:
      - ``"profile"`` (default): approved local profile data only.
      - ``"hstaff"``: legacy hstaff-mirror talent data.
    """
    denied, target = _require_target(db, email)
    if denied:
        return denied
    denied = _require_owner_or_cv_staff(user, target)
    if denied:
        return denied
    target_role = (body.get("target_role") or "").strip()
    source = (body.get("source") or "profile").strip().lower()
    try:
        if source == "hstaff":
            cv_id, html = services.generate_cv(db, target, target_role, requester=user)
        else:
            cv_id, html = services.generate_profile_cv(
                db, target, target_role, requester=user
            )
    except ValueError as e:
        return _err(str(e), 400)
    return {"cv_id": cv_id, "html": html}


# ---------- CV prefs read (owner only) ----------
# Declared before the ``{cv_id}`` route: ``/cv/prefs`` must not be captured by
# the uuid path converter below.
@router.get("/{email}/cv/prefs")
def read_prefs(
    email: str,
    target_role: str = Query("backend-developer"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    denied, target = _require_target(db, email)
    if denied:
        return denied
    denied = _require_owner(user, target)
    if denied:
        return denied
    prefs = services.get_prefs(db, target, target_role)
    if not prefs:
        return {
            "target_role": target_role,
            "hidden_sections": [],
            "project_order": [],
        }
    return {
        "target_role": prefs.target_role,
        "hidden_sections": prefs.hidden_sections or [],
        "project_order": prefs.project_order or [],
    }


# ---------- stored CV ----------
@router.get("/{email}/cv/{cv_id}")
def get_cv(
    email: str,
    cv_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    denied, target = _require_target(db, email)
    if denied:
        return denied
    if not access.can_view_cv(user, target):
        return _err("Only the profile owner or management may view stored CVs", 403)
    cv = services.get_cv(db, target, cv_id)
    if not cv:
        return _err("CV not found", 404)
    return {
        "id": str(cv.id),
        "target_role": cv.target_role,
        "sha256": cv.sha256,
        "html": cv.html,
        "snapshot": cv.snapshot,
        "created_at": cv.created_at.isoformat() if cv.created_at else None,
    }


# ---------- public (visibility-respecting) CV ----------
@router.get("/{email}/cv")
def public_cv(
    email: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Render the latest CV as a **public** (visibility-respecting) document.

    The snapshot is rebuilt fresh from approved profile data with ``public=True``
    (no personal prefs, restricted data stripped). If the profile has not been
    published via ``ProfileShare``, the request is denied.
    """
    denied, target = _require_target(db, email)
    if denied:
        return denied
    # The publication switch is the source of truth — not the snapshot contents.
    # ``build_profile_cv_snapshot`` returns a minimal snapshot (header only) for
    # unpublished profiles, so a content-based check could never distinguish an
    # unpublished profile from a published one whose sections are all disabled.
    share = db.execute(
        select(ProfileShare).where(ProfileShare.user_id == target.id)
    ).scalar_one_or_none()
    if not share or not share.is_published:
        return _err("This profile is not publicly shared", 403)
    role = _latest_role(target, db)
    snapshot = services.build_profile_cv_snapshot(
        db, target, role, prefs=None, public=True
    )
    html = services.render_public(snapshot)
    return HTMLResponse(content=html, media_type="text/html; charset=utf-8")


# ---------- CV prefs (owner only) ----------
@router.patch("/{email}/cv/prefs")
def save_prefs(
    email: str,
    body: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    denied, target = _require_target(db, email)
    if denied:
        return denied
    denied = _require_owner(user, target)
    if denied:
        return denied
    target_role = (body.get("target_role") or "").strip()
    try:
        prefs = services.save_prefs(
            db,
            target,
            target_role,
            hidden_sections=body.get("hidden_sections"),
            project_order=body.get("project_order"),
        )
    except ValueError as e:
        return _err(str(e), 400)
    return {
        "target_role": prefs.target_role,
        "hidden_sections": prefs.hidden_sections or [],
        "project_order": prefs.project_order or [],
    }


# ---------- project flag toggle (owner/manager) ----------
@router.patch("/{email}/projects/{project_id}")
def update_project_flags(
    email: str,
    project_id: uuid.UUID,
    body: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    denied, target = _require_target(db, email)
    if denied:
        return denied
    denied = _require_owner_or_manager(user, target)
    if denied:
        return denied
    project = db.execute(
        select(TalentProject).where(
            TalentProject.id == project_id,
            TalentProject.user_id == target.id,
        )
    ).scalar_one_or_none()
    if not project:
        return _err("Project not found", 404)
    if "hidden" in body:
        project.hidden = bool(body["hidden"])
    if "confidential" in body:
        project.confidential = bool(body["confidential"])
    db.commit()
    db.refresh(project)
    return {
        "id": str(project.id),
        "title": project.title,
        "hidden": project.hidden,
        "confidential": project.confidential,
    }


# ---------- hstaff sync (owner/manager) ----------
@router.post("/{email}/sync")
def sync_talent(
    email: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    denied, target = _require_target(db, email)
    if denied:
        return denied
    denied = _require_owner_or_manager(user, target)
    if denied:
        return denied
    return services.sync_from_hstaff(db, target, requester=user)


# ---------- helper for the public CV route ----------
def _latest_role(target, db: Session) -> str:
    """Pick the most recently generated target_role (falls back to the first
    template) so the public CV has a sensible section layout."""
    latest = services.latest_cv(db, target)
    if latest and latest.target_role:
        return latest.target_role
    return "backend-developer"
