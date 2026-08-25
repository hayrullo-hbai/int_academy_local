"""Student profile endpoints: skills, projects, external accounts, review queue.

Read endpoints are audience-aware — a viewer sees the *approved* version of an
item, the owner additionally sees their unreviewed edits and review status.
Write endpoints are owner-scoped (with the Academy Manager override the spec
grants in §6), and the verification endpoints are reviewer-scoped.
"""

import uuid

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.config import settings
from app.core.deps import get_current_user, get_optional_user
from app.core.responses import error as _err
from app.core.utils import resolve_user_by_ident
from app.identity.models import User
from app.profile import access, services
from app.profile.enums import (
    EVIDENCE_KIND_VALUES,
    PROFILE_SECTION_LABELS,
    PROFILE_SECTION_VALUES,
    ITEM_TYPE_VALUES,
    SKILL_CATEGORY_LABELS,
    SOCIAL_PLATFORM_VALUES,
    VISIBILITY_VALUES,
    ItemType,
    ProfileSection,
    SkillLevel,
    # ReviewState,
)
from app.profile.models import (
    ProfileCertificate,
    ProfileProject,
    ProfileSkill,
    ProfileSocialAccount,
)

router = APIRouter(prefix="/profile", tags=["profile"])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _resolve_user(db: Session, ident: str) -> User | None:
    """Accept a full email or the email local-part slug (spec §4.3).

    Uses the shared safe lookup so special characters in the ident can never
    act as SQL wildcards or cause a 500.
    """
    return resolve_user_by_ident(db, User, ident)


def _build_skill_payload(skill: ProfileSkill, *, owner_view: bool) -> dict:
    """Serialize one skill for an API response. Used by the list/create/
    update/verify skill endpoints below, and by ``_build_item_payload`` for
    the review-queue "item" block.

    Owner sees their working copy; everyone else sees the approved one.
    That is the AP-5 rule "the previously approved version stays visible when a
    verified skill is edited and sent back for review".
    """
    content = skill.resolve_content(owner_view)
    payload = {
        "id": str(skill.id),
        **content,
        "category_label": SKILL_CATEGORY_LABELS.get(content.get("category", ""), ""),
        # The stored path is an internal detail; hand the client a URL it can
        # put straight in an <img> or a link.
        # Root-relative on purpose. The browser talks to the API through the
        # frontend's /api rewrite, so request.base_url here is the internal
        # backend host (http://backend:8000) — an absolute URL built from it is
        # unreachable from a browser tab. The client prefixes its own API base.
        "attachment_url": (
            f"{settings.MEDIA_URL}{content['attachment_path']}"
            if content.get("attachment_path")
            else None
        ),
        "attachment_name": content.get("attachment_name") or "",
        **skill.build_review_payload(),
    }
    payload.pop("attachment_path", None)
    if owner_view and skill.has_pending_changes:
        # Let the owner compare what's live against what's queued.
        payload["approved_version_content"] = skill.approved_snapshot
    return payload


def _owner_skills(db: Session, user_id) -> dict:
    """``{id: ProfileSkill}`` for one profile — built once per request so a
    list of projects doesn't re-query the same skills for every row."""
    return {
        str(s.id): s
        for s in db.execute(
            select(ProfileSkill).where(ProfileSkill.user_id == user_id)
        ).scalars()
    }


def _linked_skills(by_id: dict, ids, *, owner_view: bool):
    """Resolve a project's linked skill ids to ``{id, name, is_verified}``.

    Unverified links are the owner's business only: an audience sees a project's
    verified skills or nothing, so an unproven claim never rides along on a
    project. Ids of deleted skills simply drop out.
    """
    out = []
    for sid in ids or []:
        skill = by_id.get(str(sid))
        if skill is None or (not owner_view and not skill.is_verified):
            continue
        out.append(
            {"id": str(skill.id), "name": skill.name, "is_verified": skill.is_verified}
        )
    return out


def _build_project_payload(
    db: Session, project: ProfileProject, *, owner_view: bool, skills_by_id=None
) -> dict:
    """Serialize one project for an API response. Used by the list/create/
    update project endpoints, ``_build_item_payload``, and directly by
    ``tests/test_projects.py`` to assert what an owner vs. a stranger sees.

    ``skills_by_id`` lets a project-list request resolve every row's linked
    skills from one query instead of one per project.
    """
    content = project.resolve_content(owner_view)
    if skills_by_id is None:
        skills_by_id = _owner_skills(db, project.user_id)
    skill_ids = content.pop("skill_ids", [])

    payload = {
        "id": str(project.id),
        **content,
        # Raw ids are the owner's editing handle. Everyone else gets only the
        # resolved, verified subset — echoing the ids back would expose the
        # unverified links `_linked_skills` deliberately withholds.
        **({"skill_ids": skill_ids} if owner_view else {}),
        "skills": _linked_skills(skills_by_id, skill_ids, owner_view=owner_view),
        **project.build_review_payload(),
    }
    if owner_view and project.has_pending_changes:
        payload["approved_version_content"] = project.approved_snapshot
    return payload


def _build_social_payload(account: ProfileSocialAccount, *, owner_view: bool) -> dict:
    """Serialize one external account for an API response. Used by the
    list/create/update social-account endpoints and ``_build_item_payload``."""
    content = account.resolve_content(owner_view)
    return {"id": str(account.id), **content, **account.build_review_payload()}


def _build_verification_payload(record) -> dict:
    """Serialize one ``ProfileVerification`` row. Used by the item's
    review-history endpoint and by the reviewer's review-queue endpoint,
    alongside ``_build_item_payload`` for the item being reviewed."""
    return {
        "id": str(record.id),
        "item_type": record.item_type,
        "item_id": str(record.item_id),
        "version": record.version,
        "state": record.state,
        "snapshot": record.snapshot,
        "evidence": record.evidence or [],
        "notes": record.notes,
        "rejection_reason": record.rejection_reason,
        "reviewer": (
            record.reviewer.full_name or record.reviewer.email
            if record.reviewer
            else None
        ),
        # Who this was routed to (from hstaff). None => no mentor on file, so
        # it sits in the shared queue any mentor may pick up.
        "assigned_mentor": (
            record.assigned_mentor.full_name or record.assigned_mentor.email
            if record.assigned_mentor
            else None
        ),
        "unassigned": record.assigned_mentor_id is None,
        # Junior-tier routing: True when an academy manager may also review.
        "academy_manager_review": bool(record.academy_manager_review),
        "reviewed_at": record.reviewed_at.isoformat() if record.reviewed_at else None,
        "created_at": record.created_at.isoformat(),
    }


def _approved_filter(
    items, approved_only: bool
):  # , *, section_unverified: bool = False):
    """Spec §4.1 vs §4.2: the read-only overview renders what an audience would
    actually see — the approved version — so an owner previewing their own
    profile isn't shown drafts or their unreviewed edits.

    ``section_unverified`` is the v2 curator choice: when the section opts in,
    never-approved items are shown too (rejected ones excepted)."""
    if not approved_only:
        return items
    # if section_unverified:
    #     return [
    #         i
    #         for i in items
    #         if i.approved_snapshot is not None
    #         or i.review_state != ReviewState.REJECTED.value
    #     ]
    return [i for i in items if i.approved_snapshot is not None]


def _guard(fn, *args, **kwargs):
    """Translate the service layer's exceptions into the API's error shape."""
    try:
        return None, fn(*args, **kwargs)
    except PermissionError as exc:
        return _err(str(exc), 403), None
    except ValueError as exc:
        return _err(str(exc), 400), None


# ---------------------------------------------------------------------------
# vocabularies — lets the frontend build its selects without hardcoding
# ---------------------------------------------------------------------------
@router.get("/meta/vocabularies")
def vocabularies():
    return {
        "skill_categories": [
            {"value": v, "label": label} for v, label in SKILL_CATEGORY_LABELS.items()
        ],
        # Declared in ascending difficulty order (beginner -> expert) rather
        # than alphabetically, so selects/UI don't scramble the progression.
        "skill_levels": [lv.value for lv in SkillLevel],
        "evidence_kinds": sorted(EVIDENCE_KIND_VALUES),
        "social_platforms": sorted(SOCIAL_PLATFORM_VALUES),
        "visibility_levels": sorted(VISIBILITY_VALUES),
        "profile_sections": [
            {"value": v, "label": label} for v, label in PROFILE_SECTION_LABELS.items()
        ],
    }


# ---------------------------------------------------------------------------
# share profile (shareProfile.md §6 + §8) — Academy Manager / Admin only
# ---------------------------------------------------------------------------
def _share_payload(share, *, can_manage: bool) -> dict:
    """Normalized share state: every section carries an audience config, so the
    frontend never deals with legacy ``{section: bool}`` rows."""
    return {
        "is_published": share.is_published,
        "sections": {
            section: {
                "visibility": share.section_visibility(section),
                # "include_unverified": share.include_unverified(section),
            }
            for section in sorted(PROFILE_SECTION_VALUES)
        },
        "can_manage": can_manage,
        "updated_by": (
            share.updated_by.full_name or share.updated_by.email
            if share.updated_by
            else None
        ),
        "updated_at": share.updated_at.isoformat() if share.updated_at else None,
    }


@router.get("/{ident}/share")
def get_share(
    ident: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Read a profile's sharing state.

    Readable by the owner (so they can see what is exposed, per §8 "the owner
    does not control public display" — they may still *look*) and by curators,
    who are the only ones who may change it.
    """
    target = _resolve_user(db, ident)
    if not target:
        return _err("User not found", 404)
    if not access.is_owner(user, target.id) and not access.can_share_profile(user):
        return _err("You can't view this profile's sharing settings", 403)
    share = services.get_share(db, target.id)
    return _share_payload(share, can_manage=access.can_share_profile(user))


@router.put("/{ident}/share")
def update_share(
    ident: str,
    body: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Publish/unpublish and choose which sections are publicly visible."""
    target = _resolve_user(db, ident)
    if not target:
        return _err("User not found", 404)

    err, share = _guard(
        services.update_share,
        db,
        target.id,
        user,
        is_published=body.get("is_published"),
        sections=body.get("sections"),
        reason=body.get("reason", ""),
    )
    if err:
        return err
    return _share_payload(share, can_manage=True)


# ---------------------------------------------------------------------------
# skills (AP-5 / AP-6)
# ---------------------------------------------------------------------------
@router.get("/{ident}/skills")
def list_skills(
    ident: str,
    search: str = Query(default=""),
    category: str = Query(default=""),
    approved_only: bool = Query(default=False),
    viewer: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    target = _resolve_user(db, ident)
    if not target:
        return _err("User not found", 404)
    share = services.get_share(db, target.id)
    if not access.can_view_section(viewer, share, ProfileSection.SKILLS.value):
        return []
    # section_unverified = share.include_unverified(ProfileSection.SKILLS.value)
    owner_view = access.can_edit_items(viewer, target.id) and not approved_only
    skills = services.list_skills(db, target.id, search=search, category=category)
    visible = [
        s
        for s in skills
        if access.can_view_item(
            viewer, target.id, s  # , section_unverified=section_unverified
        )
    ]
    visible = _approved_filter(
        visible, approved_only
    )  # , section_unverified=section_unverified)
    return [_build_skill_payload(s, owner_view=owner_view) for s in visible]


@router.post("/me/skills")
def create_skill(
    body: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    err, skill = _guard(services.create_skill, db, user.id, body, user)
    if err:
        return err
    return JSONResponse(_build_skill_payload(skill, owner_view=True), status_code=201)


@router.patch("/me/skills/{skill_id}")
def update_skill(
    skill_id: uuid.UUID,
    body: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    skill = db.get(ProfileSkill, skill_id)
    if not skill:
        return _err("Skill not found", 404)
    err, updated = _guard(services.update_skill, db, skill, body, user)
    if err:
        return err
    return _build_skill_payload(updated, owner_view=True)


@router.delete("/me/skills/{skill_id}")
def delete_skill(
    skill_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    skill = db.get(ProfileSkill, skill_id)
    if not skill:
        return _err("Skill not found", 404)
    err, _ = _guard(services.delete_skill, db, skill, user)
    return err or Response(status_code=204)


@router.put("/me/skills/{skill_id}/attachment")
def set_skill_attachment(
    skill_id: uuid.UUID,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Attach an optional supporting image or PDF (max 10MB) to a skill."""
    skill = db.get(ProfileSkill, skill_id)
    if not skill:
        return _err("Skill not found", 404)
    err, updated = _guard(services.set_skill_attachment, db, skill, file, user)
    if err:
        return err
    return _build_skill_payload(updated, owner_view=True)


@router.delete("/me/skills/{skill_id}/attachment")
def clear_skill_attachment(
    skill_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    skill = db.get(ProfileSkill, skill_id)
    if not skill:
        return _err("Skill not found", 404)
    err, updated = _guard(services.clear_skill_attachment, db, skill, user)
    if err:
        return err
    return _build_skill_payload(updated, owner_view=True)


# ---------------------------------------------------------------------------
# projects (AP-7)
# ---------------------------------------------------------------------------
@router.get("/{ident}/projects")
def list_projects(
    ident: str,
    approved_only: bool = Query(default=False),
    viewer: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    target = _resolve_user(db, ident)
    if not target:
        return _err("User not found", 404)
    share = services.get_share(db, target.id)
    if not access.can_view_section(viewer, share, ProfileSection.PROJECTS.value):
        return []
    # section_unverified = share.include_unverified(ProfileSection.PROJECTS.value)
    owner_view = access.can_edit_items(viewer, target.id) and not approved_only
    projects = services.list_projects(db, target.id)
    visible = [
        p
        for p in projects
        if access.can_view_item(
            viewer, target.id, p  # , section_unverified=section_unverified
        )
    ]
    visible = _approved_filter(
        visible, approved_only
    )  # , section_unverified=section_unverified)
    skills_by_id = _owner_skills(db, target.id)
    return [
        _build_project_payload(db, p, owner_view=owner_view, skills_by_id=skills_by_id)
        for p in visible
    ]


@router.post("/me/projects")
def create_project(
    body: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    err, project = _guard(services.create_project, db, user.id, body, user)
    if err:
        return err
    return JSONResponse(
        _build_project_payload(db, project, owner_view=True), status_code=201
    )


@router.patch("/me/projects/{project_id}")
def update_project(
    project_id: uuid.UUID,
    body: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = db.get(ProfileProject, project_id)
    if not project:
        return _err("Project not found", 404)
    err, updated = _guard(services.update_project, db, project, body, user)
    if err:
        return err
    return _build_project_payload(db, updated, owner_view=True)


@router.delete("/me/projects/{project_id}")
def delete_project(
    project_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = db.get(ProfileProject, project_id)
    if not project:
        return _err("Project not found", 404)
    err, _ = _guard(services.delete_project, db, project, user)
    return err or Response(status_code=204)


# ---------------------------------------------------------------------------
# social / external accounts (AP-8)
# ---------------------------------------------------------------------------
@router.get("/{ident}/social-accounts")
def list_social_accounts(
    ident: str,
    approved_only: bool = Query(default=False),
    viewer: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    target = _resolve_user(db, ident)
    if not target:
        return _err("User not found", 404)
    share = services.get_share(db, target.id)
    if not access.can_view_section(
        viewer, share, ProfileSection.EXTERNAL_ACCOUNTS.value
    ):
        return []
    # section_unverified = share.include_unverified(
    #     ProfileSection.EXTERNAL_ACCOUNTS.value
    # )
    owner_view = access.can_edit_items(viewer, target.id) and not approved_only
    accounts = services.list_social_accounts(db, target, viewer)
    visible = [
        a
        for a in accounts
        if access.can_view_item(
            viewer, target.id, a  # , section_unverified=section_unverified
        )
    ]
    visible = _approved_filter(
        visible, approved_only
    )  # , section_unverified=section_unverified)
    return [_build_social_payload(a, owner_view=owner_view) for a in visible]


@router.post("/me/social-accounts")
def create_social_account(
    body: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    err, account = _guard(services.create_social_account, db, user.id, body, user)
    if err:
        return err
    return JSONResponse(
        _build_social_payload(account, owner_view=True), status_code=201
    )


@router.patch("/me/social-accounts/{account_id}")
def update_social_account(
    account_id: uuid.UUID,
    body: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = db.get(ProfileSocialAccount, account_id)
    if not account:
        return _err("Account not found", 404)
    err, updated = _guard(services.update_social_account, db, account, body, user)
    if err:
        return err
    return _build_social_payload(updated, owner_view=True)


@router.delete("/me/social-accounts/{account_id}")
def delete_social_account(
    account_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = db.get(ProfileSocialAccount, account_id)
    if not account:
        return _err("Account not found", 404)
    err, _ = _guard(services.delete_social_account, db, account, user)
    return err or Response(status_code=204)


# ---------------------------------------------------------------------------
# certificates
# ---------------------------------------------------------------------------
def _build_certificate_payload(cert: ProfileCertificate, *, owner_view: bool) -> dict:
    """Serialize one certificate for an API response. Used by the
    list/create/update certificate endpoints and ``_build_item_payload``.

    Same owner/approved split as every other item.

    Two URLs come back for the file. ``file_url`` points at the static media
    mount and is what an <img> or an inline PDF frame uses; ``download_url`` is
    the API route that re-checks visibility and forces a save-as with the
    original filename. Viewers other than the owner get the approved file, so a
    swapped-but-unreviewed document is never the one that downloads.
    """
    content = cert.resolve_content(owner_view)
    payload = {
        "id": str(cert.id),
        **content,
        "file_url": (
            f"{settings.MEDIA_URL}{content['file_path']}"
            if content.get("file_path")
            else None
        ),
        "download_url": f"/profile/certificates/{cert.id}/download",
        "is_pdf": (content.get("file_type") or "").endswith("pdf"),
        **cert.build_review_payload(),
    }
    payload.pop("file_path", None)
    if owner_view and cert.has_pending_changes:
        payload["approved_version_content"] = cert.approved_snapshot
    return payload


def _certificate_view_guard(db: Session, cert: ProfileCertificate, viewer):
    """Whether ``viewer`` may read this certificate, section rules included."""
    share = services.get_share(db, cert.user_id)
    if not access.can_view_section(
        viewer, share, ProfileSection.CERTIFICATIONS.value, cert.user_id
    ):
        return False
    # section_unverified = share.include_unverified(ProfileSection.CERTIFICATIONS.value)
    return access.can_view_item(
        viewer, cert.user_id, cert  # , section_unverified=section_unverified
    )


@router.get("/{ident}/certificates")
def list_certificates(
    ident: str,
    approved_only: bool = Query(default=False),
    viewer: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    target = _resolve_user(db, ident)
    if not target:
        return _err("User not found", 404)
    share = services.get_share(db, target.id)
    if not access.can_view_section(
        viewer, share, ProfileSection.CERTIFICATIONS.value, target.id
    ):
        return []
    # section_unverified = share.include_unverified(ProfileSection.CERTIFICATIONS.value)
    owner_view = access.can_edit_items(viewer, target.id) and not approved_only
    certs = services.list_certificates(db, target.id)
    visible = [
        c
        for c in certs
        if access.can_view_item(
            viewer, target.id, c  # , section_unverified=section_unverified
        )
    ]
    visible = _approved_filter(
        visible, approved_only
    )  # , section_unverified=section_unverified)
    return [_build_certificate_payload(c, owner_view=owner_view) for c in visible]


@router.post("/me/certificates")
def create_certificate(
    title: str = Form(...),
    slug: str = Form(default=""),
    visibility: str = Form(default=""),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upload a certificate: an image (max 10MB) plus its title.

    Multipart rather than JSON-then-upload, because a certificate without its
    document has nothing to show and nothing to review — the two arrive together
    or not at all.
    """
    body = {"title": title, "slug": slug}
    if visibility:
        body["visibility"] = visibility
    err, cert = _guard(services.create_certificate, db, user.id, body, file, user)
    if err:
        return err
    return JSONResponse(
        _build_certificate_payload(cert, owner_view=True), status_code=201
    )


@router.patch("/me/certificates/{certificate_id}")
def update_certificate(
    certificate_id: uuid.UUID,
    body: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cert = db.get(ProfileCertificate, certificate_id)
    if not cert:
        return _err("Certificate not found", 404)
    err, updated = _guard(services.update_certificate, db, cert, body, user)
    if err:
        return err
    return _build_certificate_payload(updated, owner_view=True)


@router.put("/me/certificates/{certificate_id}/file")
def replace_certificate_file(
    certificate_id: uuid.UUID,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Swap the document. Counts as an edit, so a verified certificate drops
    back to Draft and the previously approved file keeps serving readers."""
    cert = db.get(ProfileCertificate, certificate_id)
    if not cert:
        return _err("Certificate not found", 404)
    err, updated = _guard(services.set_certificate_file, db, cert, file, user)
    if err:
        return err
    return _build_certificate_payload(updated, owner_view=True)


@router.delete("/me/certificates/{certificate_id}")
def delete_certificate(
    certificate_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cert = db.get(ProfileCertificate, certificate_id)
    if not cert:
        return _err("Certificate not found", 404)
    err, _ = _guard(services.delete_certificate, db, cert, user)
    return err or Response(status_code=204)


@router.get("/certificates/{certificate_id}/download")
def download_certificate(
    certificate_id: uuid.UUID,
    viewer: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """Download a certificate file.

    The media mount serves these files by unguessable path; this route is what
    makes them *shareable* — it re-checks the section and item visibility, and
    hands the file back as an attachment named the way the owner uploaded it.
    Everyone who may see the certificate may download it.
    """
    cert = db.get(ProfileCertificate, certificate_id)
    if not cert:
        return _err("Certificate not found", 404)
    if not _certificate_view_guard(db, cert, viewer):
        return _err("Certificate not found", 404)

    # Non-owners get the approved document, matching what the list endpoint
    # showed them — an unreviewed replacement isn't published yet.
    content = cert.resolve_content(access.can_edit_items(viewer, cert.user_id))
    rel = content.get("file_path") or ""
    path = settings.MEDIA_ROOT / rel if rel else None
    if not rel or not path.exists():
        return _err("This certificate has no file", 404)
    return FileResponse(
        path,
        media_type=content.get("file_type") or "application/octet-stream",
        filename=content.get("file_name") or f"{content.get('slug') or 'certificate'}",
    )


# ---------------------------------------------------------------------------
# verification workflow (AP-9)
# ---------------------------------------------------------------------------
def _build_item_payload(db: Session, item_type: str, item, viewer) -> dict:
    """Dispatch to the right ``_build_*_payload`` for whatever item type is
    on the review queue / verification-history / act-on-item endpoints,
    always as its owner would see it (those endpoints are reviewer-only, so
    the reviewer needs the full working-copy view to judge the submission)."""
    if item_type == ItemType.PROJECT.value:
        return _build_project_payload(db, item, owner_view=True)
    if item_type == ItemType.SKILL.value:
        return _build_skill_payload(item, owner_view=True)
    if item_type == ItemType.CERTIFICATE.value:
        return _build_certificate_payload(item, owner_view=True)
    return _build_social_payload(item, owner_view=True)


def _load(db: Session, item_type: str, item_id):
    if item_type not in ITEM_TYPE_VALUES:
        return None, _err("Unknown item type", 400)
    item = services.get_item(db, item_type, item_id)
    if not item:
        return None, _err("Item not found", 404)
    return item, None


@router.post("/me/{item_type}/{item_id}/submit")
def submit_for_review(
    item_type: str,
    item_id: uuid.UUID,
    body: dict = Body(default={}),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Draft / Rejected → Submitted, with optional supporting evidence (AP-6)."""
    item, err = _load(db, item_type, item_id)
    if err:
        return err
    err, _ = _guard(
        services.submit_for_review, db, item_type, item, user, body.get("evidence")
    )
    if err:
        return err
    return _build_item_payload(db, item_type, item, user)


@router.get("/me/{item_type}/{item_id}/history")
def item_history(
    item_type: str,
    item_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Every review this item has been through — the version metadata AP-6
    asks to be retained alongside the approved record."""
    item, err = _load(db, item_type, item_id)
    if err:
        return err
    if not access.can_edit_items(user, item.user_id) and not access.can_verify(user):
        return _err("You can't view this item's history", 403)
    return [
        _build_verification_payload(r) for r in services.history(db, item_type, item_id)
    ]


@router.get("/reviews/queue")
def reviews_queue(
    item_type: str = Query(default=""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Everything awaiting verification, for mentors and academy managers."""
    try:
        entries = services.review_queue(db, user, item_type=item_type or None)
    except PermissionError as exc:
        return _err(str(exc), 403)
    return [
        {
            "record": _build_verification_payload(e["record"]),
            "item": _build_item_payload(db, e["record"].item_type, e["item"], user),
            "owner": {
                "id": str(e["item"].user.id),
                "email": e["item"].user.email,
                "full_name": e["item"].user.full_name,
            },
            "can_act": services.can_act_on(e["record"], user, e["item"]),
        }
        for e in entries
    ]


@router.post("/reviews/{item_type}/{item_id}/start")
def start_review(
    item_type: str,
    item_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    item, err = _load(db, item_type, item_id)
    if err:
        return err
    err, _ = _guard(services.start_review, db, item_type, item, user)
    if err:
        return err
    return _build_item_payload(db, item_type, item, user)


@router.post("/reviews/{item_type}/{item_id}/approve")
def approve_item(
    item_type: str,
    item_id: uuid.UUID,
    body: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Approve with verification details (notes, evidence) — AP-9."""
    item, err = _load(db, item_type, item_id)
    if err:
        return err
    err, _ = _guard(
        services.approve,
        db,
        item_type,
        item,
        user,
        notes=body.get("notes", ""),
        evidence=body.get("evidence"),
    )
    if err:
        return err
    return _build_item_payload(db, item_type, item, user)


@router.post("/reviews/{item_type}/{item_id}/reject")
def reject_item(
    item_type: str,
    item_id: uuid.UUID,
    body: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Reject with a reason. The last approved version stays visible — AP-9."""
    item, err = _load(db, item_type, item_id)
    if err:
        return err
    err, _ = _guard(
        services.reject, db, item_type, item, user, reason=body.get("reason", "")
    )
    if err:
        return err
    return _build_item_payload(db, item_type, item, user)


# ---------------------------------------------------------------------------
# staff visibility / publication overrides
# ---------------------------------------------------------------------------
@router.post("/reviews/{item_type}/{item_id}/override-visibility")
def override_visibility(
    item_type: str,
    item_id: uuid.UUID,
    body: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Academy Manager / Admin override of an item's visibility, outside the
    item's normal edit path. Requires a reason; notifies the profile owner.
    """
    item, err = _load(db, item_type, item_id)
    if err:
        return err
    err, _ = _guard(
        services.override_visibility,
        db,
        item_type,
        item,
        user,
        visibility=body.get("new_visibility"),
        reason=body.get("reason", ""),
    )
    if err:
        return err
    return _build_item_payload(db, item_type, item, user)
