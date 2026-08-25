"""Profile item services: CRUD plus the shared verification workflow.

The workflow (spec §7, ticket AP-9) is the interesting part. Its one invariant:

    An approved version stays visible until a newer version is approved.

Everything else follows from that. ``submit`` snapshots the working copy into a
``ProfileVerification`` row; ``approve`` promotes that snapshot onto the item's
``approved_snapshot``; ``reject`` leaves the previous approval untouched. An
edit to a verified item bumps ``version`` and drops the item back to DRAFT
while ``approved_snapshot`` keeps serving readers.
"""

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.files import ATTACHMENT_CONTENT_TYPES, delete_media, save_attachment
from app.identity.models import User
from app.profile import access, hstaff_sync, mentorship, notifications
from app.profile.enums import (
    DEFAULT_SECTION_SHARING,
    EVIDENCE_KIND_VALUES,
    PROFILE_SECTION_VALUES,
    ITEM_TYPE_VALUES,
    SKILL_CATEGORY_VALUES,
    SKILL_LEVEL_VALUES,
    SOCIAL_PLATFORM_VALUES,
    VISIBILITY_VALUES,
    ProfileSection,
    ReviewState,
    SkillLevel,
    SocialPlatform,
    Visibility,
)
from app.profile.models import (
    ITEM_MODELS,
    ProfileCertificate,
    ProfileShare,
    ProfileProject,
    ProfileSkill,
    ProfileSocialAccount,
    ProfileVerification,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean_list(value, limit: int = 50) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out[:limit]


# ---------------------------------------------------------------------------
# Shared workflow (AP-9)
# ---------------------------------------------------------------------------
# Tier-based routing for the verification workflow. Junior tiers are reviewed
# by the student's mentor AND an academy manager; senior tiers go to the
# mentor alone;
# any other role never needs verification (submissions are auto-approved on
# submit). "fundamental" / "growth3" are hstaff tier names that arrive via
# the login role mirror even though they aren't in the local RBAC seed
# catalog (identity.rbac_catalog).
ACADEMY_MANAGER_TIERS = {"school", "softlanding", "foundation", "fundamental"}
MENTOR_ONLY_TIERS = {"talent", "growth3"}


def _review_tier(student) -> str | None:
    """Which review route a student's submission takes.

    Returns ``"academy_manager"`` (mentor + academy manager), ``"mentor_only"``
    (the student's own mentor), or None when the student's roles don't require verification at
    all (their submission is auto-approved). A student holding a senior tier is
    treated as senior even when they also carry a junior one (e.g. both
    ``talent`` and ``foundation``).
    """
    if student is None:
        return None
    roles = set(student.role_names)
    if roles & MENTOR_ONLY_TIERS:
        return "mentor_only"
    if roles & ACADEMY_MANAGER_TIERS:
        return "academy_manager"
    # if not roles: #we do not need this case, in future development , no role users can not edit profile
    #     return "admin_only"
    return None


def get_item(db: Session, item_type: str, item_id):
    if item_type not in ITEM_TYPE_VALUES:
        raise ValueError("Unknown item type")
    return db.get(ITEM_MODELS[item_type], item_id)


def _validate_evidence(evidence) -> list[dict]:
    """Evidence entries are ``{kind, reference, note}`` (spec §8)."""
    if evidence is None:
        return []
    if not isinstance(evidence, list):
        raise ValueError("Evidence must be a list")
    out = []
    for raw in evidence[:20]:
        if not isinstance(raw, dict):
            raise ValueError("Each evidence entry must be an object")
        kind = (raw.get("kind") or "").strip()
        if kind not in EVIDENCE_KIND_VALUES:
            raise ValueError(f"Unsupported evidence type: {kind or '(missing)'}")
        out.append(
            {
                "kind": kind,
                "reference": str(raw.get("reference") or "").strip()[:500],
                "note": str(raw.get("note") or "").strip()[:1000],
            }
        )
    return out


def submit_for_review(
    db: Session, item_type: str, item, requester, evidence=None
) -> ProfileVerification | None:
    """Move an item Draft/Rejected → Submitted and open a verification record.

    Returns None when the item's owner doesn't need verification (their tier
    isn't routed to any reviewer) — the item is auto-approved instead.
    """
    if not access.can_edit_items(requester, item.user_id):
        raise PermissionError("You can't submit this item for review")
    if item.review_state in (
        ReviewState.SUBMITTED.value,
        ReviewState.UNDER_REVIEW.value,
    ):
        raise ValueError("This item is already awaiting review")
    if item.review_state == ReviewState.VERIFIED.value and not item.has_pending_changes:
        raise ValueError("This item is already verified")

    entries = _validate_evidence(evidence)
    item.review_state = ReviewState.SUBMITTED.value
    item.submitted_at = _now()
    item.rejection_reason = ""

    # Route by the owner's tier. Junior tiers (school / softlanding /
    # foundation / fundamental) are verified by the mentor AND an academy
    # manager; senior tiers (talent / growth3) by the mentor alone; users with
    # no roles at all are verified by admin alone. Anyone with a non-learner
    # role doesn't need verification — the item goes live at once.
    student = item.user or db.get(User, item.user_id)
    tier = _review_tier(student)
    if tier is None:
        item.auto_verify()
        db.commit()
        return None

    # hstaff owns the mentor↔mentee relationship, so we resolve it at submit
    # time and pin it to the record — a later reassignment in hstaff shouldn't
    # silently move a submission that's already queued. Admin-only submissions
    # (no roles on file) skip the mentor lookup: they're reviewed by admin.
    mentor = (
        mentorship.lookup_mentor(db, student)
        if student  # and tier != "admin_only" #no need
        else None
    )

    record = ProfileVerification(
        item_type=item_type,
        item_id=item.id,
        user_id=item.user_id,
        version=item.version,
        state=ReviewState.SUBMITTED.value,
        snapshot=item.snapshot(),
        evidence=entries,
        assigned_mentor_id=mentor.id if mentor else None,
        academy_manager_review=tier == "academy_manager",
    )
    db.add(record)
    db.commit()
    return record


def _open_record(db: Session, item_type: str, item) -> ProfileVerification | None:
    """The verification row for the version currently under review."""
    return (
        db.execute(
            select(ProfileVerification)
            .where(
                ProfileVerification.item_type == item_type,
                ProfileVerification.item_id == item.id,
                ProfileVerification.version == item.version,
                ProfileVerification.state.in_(
                    [ReviewState.SUBMITTED.value, ReviewState.UNDER_REVIEW.value]
                ),
            )
            .order_by(ProfileVerification.created_at.desc())
        )
        .scalars()
        .first()
    )


def start_review(db: Session, item_type: str, item, reviewer):
    """Submitted → Under Review, so the owner can see it's been picked up."""
    if not access.can_verify(reviewer):
        raise PermissionError("You can't review profile items")
    if item.review_state != ReviewState.SUBMITTED.value:
        raise ValueError("Only a submitted item can be moved to review")
    record = _open_record(db, item_type, item)
    if record and not can_act_on(record, reviewer, item):
        raise PermissionError("This submission is assigned to another mentor")
    item.review_state = ReviewState.UNDER_REVIEW.value
    if record:
        record.state = ReviewState.UNDER_REVIEW.value
        record.reviewer_id = reviewer.id
    db.commit()
    return item


def approve(
    db: Session,
    item_type: str,
    item,
    reviewer,
    *,
    notes: str = "",
    evidence=None,
):
    """Approve the version under review and promote it to the approved copy."""
    if not access.can_verify(reviewer):
        raise PermissionError("You can't verify profile items")
    if not item.is_pending_review:
        raise ValueError("This item isn't awaiting review")
    extra = _validate_evidence(evidence)

    record = _open_record(db, item_type, item)
    if record and not can_act_on(record, reviewer, item):
        raise PermissionError("This submission is assigned to another mentor")
    if record:
        record.state = ReviewState.VERIFIED.value
        record.reviewer_id = reviewer.id
        record.reviewed_at = _now()
        record.notes = (notes or "").strip()
        if extra:
            record.evidence = list(record.evidence or []) + extra

    # Promote: this version becomes the one everyone sees.
    item.review_state = ReviewState.VERIFIED.value
    item.approved_snapshot = item.snapshot()
    item.approved_version = item.version
    item.reviewer_id = reviewer.id
    item.reviewed_at = _now()
    item.verification_notes = (notes or "").strip()
    item.rejection_reason = ""
    # Spec §10 lifecycle is Draft → (mentor approval) → Private → (Academy
    # Manager) → Public. Items already default to Private visibility, so
    # approval is what makes them *readable* (via can_view_item) without
    # touching the level — raising it stays the Academy Manager's call.
    db.commit()
    return item


def reject(db: Session, item_type: str, item, reviewer, *, reason: str):
    """Reject the pending version. Any previous approval survives untouched."""
    if not access.can_verify(reviewer):
        raise PermissionError("You can't verify profile items")
    if not item.is_pending_review:
        raise ValueError("This item isn't awaiting review")
    clean = (reason or "").strip()
    if not clean:
        raise ValueError("A rejection reason is required")

    record = _open_record(db, item_type, item)
    if record and not can_act_on(record, reviewer, item):
        raise PermissionError("This submission is assigned to another mentor")
    if record:
        record.state = ReviewState.REJECTED.value
        record.reviewer_id = reviewer.id
        record.reviewed_at = _now()
        record.rejection_reason = clean

    item.review_state = ReviewState.REJECTED.value
    item.rejection_reason = clean
    item.submitted_at = None
    # approved_snapshot / approved_version are intentionally left alone: the
    # last approved version stays visible while the owner corrects and resubmits.
    db.commit()
    return item


def history(db: Session, item_type: str, item_id) -> list[ProfileVerification]:
    return list(
        db.execute(
            select(ProfileVerification)
            .where(
                ProfileVerification.item_type == item_type,
                ProfileVerification.item_id == item_id,
            )
            .order_by(ProfileVerification.created_at.desc())
        ).scalars()
    )


def can_act_on(record, reviewer, item=None) -> bool:
    """Whether ``reviewer`` may approve/reject this particular submission.

    Routing is automatic — the student only has to submit:

    * junior-tier submission (academy_manager_review) → the assigned mentor,
      every academy manager, and admin
    * senior-tier submission → the assigned mentor and admin only
    * no roles on file (admin_only) → admin only — no mentor, no academy
      manager
    * no mentor on file → for junior tiers the academy managers are the
      fallback pool; for senior tiers admin is the fallback. It deliberately
      does *not* fall back to "any mentor": that would let one mentee's work
      land in an unrelated mentor's queue.

    Nobody reviews their own submission, admins included — a superadmin with
    no mentor of their own would otherwise self-approve, since their
    submissions are unassigned by construction.
    """
    if not access.can_verify(reviewer):
        return False
    if item is not None and access.is_owner(reviewer, item.user_id):
        return False
    if access.is_admin(reviewer):
        return True  # fallback whenever a mentor leaves or is offboarded
    if access.is_curator(reviewer):
        return bool(record.academy_manager_review)
    if record.assigned_mentor_id is None:
        return False
    return str(record.assigned_mentor_id) == str(reviewer.id)


def review_queue(db: Session, reviewer, *, item_type: str | None = None) -> list[dict]:
    """Submissions awaiting review, newest first.

    Mentors see only the submissions routed to them; academy managers see the
    junior-tier submissions routed to them (and are the automatic fallback for
    junior-tier students with no mentor on file); admin sees everything — the
    fallback for students whose tier routes to nobody else, including students
    with no roles on file. Your own submissions never appear — see
    :func:`can_act_on`.
    """
    if not access.can_verify(reviewer):
        raise PermissionError("You can't review profile items")
    stmt = select(ProfileVerification).where(
        ProfileVerification.state.in_(
            [ReviewState.SUBMITTED.value, ReviewState.UNDER_REVIEW.value]
        )
    )
    stmt = stmt.where(ProfileVerification.user_id != reviewer.id)
    if access.is_admin(reviewer):
        pass  # superadmin sees every submission
    elif access.is_curator(reviewer):
        # Academy managers only see junior-tier submissions — senior-tier
        # reviews go to the student's own mentor (and admin).
        stmt = stmt.where(ProfileVerification.academy_manager_review.is_(True))
    else:
        stmt = stmt.where(ProfileVerification.assigned_mentor_id == reviewer.id)
    rows = list(
        db.execute(stmt.order_by(ProfileVerification.created_at.desc())).scalars()
    )
    if item_type:
        rows = [r for r in rows if r.item_type == item_type]

    out = []
    for record in rows:
        item = get_item(db, record.item_type, record.item_id)
        if item is None or item.version != record.version:
            continue  # superseded by a newer edit
        out.append({"record": record, "item": item})
    return out


# ---------------------------------------------------------------------------
# Skills (AP-5 / AP-6)
# ---------------------------------------------------------------------------
def list_skills(db: Session, user_id, *, search: str = "", category: str = ""):
    stmt = select(ProfileSkill).where(ProfileSkill.user_id == user_id)
    if category:
        stmt = stmt.where(ProfileSkill.category == category)
    if search:
        needle = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            or_(
                ProfileSkill.search_key.like(needle),
                ProfileSkill.category.like(needle),
            )
        )
    stmt = stmt.order_by(ProfileSkill.category, ProfileSkill.name)
    return list(db.execute(stmt).scalars())


def _apply_skill_fields(skill: ProfileSkill, body: dict, *, creating: bool) -> None:
    if creating or "name" in body:
        name = (body.get("name") or "").strip()
        if not name:
            raise ValueError("A skill name is required")
        skill.name = name[:100]
        skill.search_key = skill.name.lower()
    if creating or "category" in body:
        category = (body.get("category") or "").strip()
        if category not in SKILL_CATEGORY_VALUES:
            raise ValueError("A valid skill category is required")
        skill.category = category
    if creating or "level" in body:
        level = (body.get("level") or SkillLevel.BEGINNER.value).strip()
        if level not in SKILL_LEVEL_VALUES:
            raise ValueError("Unknown skill level")
        skill.level = level
    if "proficiency_note" in body:
        skill.proficiency_note = str(body.get("proficiency_note") or "").strip()[:1000]
    if "related_technologies" in body:
        skill.related_technologies = _clean_list(body.get("related_technologies"), 30)
    if "notes" in body:
        skill.notes = str(body.get("notes") or "").strip()[:2000]


def create_skill(db: Session, user_id, body: dict, requester) -> ProfileSkill:
    if not access.can_edit_items(requester, user_id):
        raise PermissionError("You can't edit this profile")
    skill = ProfileSkill(user_id=user_id)
    _apply_skill_fields(skill, body, creating=True)
    existing = db.execute(
        select(ProfileSkill).where(
            ProfileSkill.user_id == user_id, ProfileSkill.name == skill.name
        )
    ).scalar_one_or_none()
    if existing:
        raise ValueError(f"'{skill.name}' is already on this profile")
    db.add(skill)
    db.flush()  # assign skill.id before visibility is set
    _set_visibility(db, skill, body.get("visibility"), requester)
    db.commit()
    return skill


def update_skill(
    db: Session, skill: ProfileSkill, body: dict, requester
) -> ProfileSkill:
    if not access.can_edit_items(requester, skill.user_id):
        raise PermissionError("You can't edit this profile")
    before = skill.snapshot()
    _apply_skill_fields(skill, body, creating=False)
    if "visibility" in body:
        _set_visibility(db, skill, body.get("visibility"), requester)
    if skill.snapshot() != before:
        skill.mark_dirty()
    db.commit()
    return skill


def delete_skill(db: Session, skill: ProfileSkill, requester) -> None:
    if not access.can_edit_items(requester, skill.user_id):
        raise PermissionError("You can't edit this profile")
    _purge_verifications(db, "skill", skill.id)
    _unlink_skill_from_projects(db, skill)
    delete_media(skill.attachment_path)
    db.delete(skill)
    db.commit()


def set_skill_attachment(
    db: Session, skill: ProfileSkill, file, requester
) -> ProfileSkill:
    """Attach (or replace) the skill's optional supporting image / PDF.

    Validation lives in :func:`save_attachment`; its ValueErrors carry the
    message the user sees. Replacing removes the previous file so orphans don't
    accumulate in the media dir.
    """
    if not access.can_edit_items(requester, skill.user_id):
        raise PermissionError("You can't edit this profile")
    if file is None:
        raise ValueError("A file is required")
    before = skill.snapshot()
    previous = skill.attachment_path
    skill.attachment_path = save_attachment(file, "skill_attachments")
    skill.attachment_name = (file.filename or "attachment")[:200]
    if skill.snapshot() != before:
        skill.mark_dirty()
    db.commit()
    if previous and previous != skill.attachment_path:
        delete_media(previous)
    return skill


def clear_skill_attachment(db: Session, skill: ProfileSkill, requester) -> ProfileSkill:
    if not access.can_edit_items(requester, skill.user_id):
        raise PermissionError("You can't edit this profile")
    if not skill.attachment_path:
        return skill
    previous = skill.attachment_path
    before = skill.snapshot()
    skill.attachment_path = ""
    skill.attachment_name = ""
    if skill.snapshot() != before:
        skill.mark_dirty()
    db.commit()
    delete_media(previous)
    return skill


def _unlink_skill_from_projects(db: Session, skill: ProfileSkill) -> None:
    """Drop a deleted skill from every project that linked it.

    ``skill_ids`` is a JSON list, not an FK, so nothing else removes the id.
    Readers already skip ids they can't resolve; this stops the stored list
    from accumulating references to skills that no longer exist.
    """
    dead = str(skill.id)
    for project in db.execute(
        select(ProfileProject).where(ProfileProject.user_id == skill.user_id)
    ).scalars():
        current = [str(s) for s in (project.skill_ids or [])]
        if dead in current:
            project.skill_ids = [s for s in current if s != dead]


# ---------------------------------------------------------------------------
# Projects (AP-7)
# ---------------------------------------------------------------------------
def list_projects(db: Session, user_id):
    return list(
        db.execute(
            select(ProfileProject)
            .where(ProfileProject.user_id == user_id)
            .order_by(ProfileProject.present.desc(), ProfileProject.start_date.desc())
        ).scalars()
    )


_DATE_RE = re.compile(r"^\d{4}(-\d{2}){0,2}$")


def _clean_date(value, field: str) -> str:
    """Accept YYYY, YYYY-MM or YYYY-MM-DD.

    The shape check alone isn't enough — it happily accepts "2021-45" — so the
    parts are range-checked against the real calendar too.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if not _DATE_RE.match(text):
        raise ValueError(f"{field} must look like YYYY, YYYY-MM or YYYY-MM-DD")
    parts = [int(p) for p in text.split("-")]
    year = parts[0]
    if not 1900 <= year <= 2100:
        raise ValueError(f"{field} must have a year between 1900 and 2100")
    if len(parts) > 1 and not 1 <= parts[1] <= 12:
        raise ValueError(f"{field} has an invalid month ({parts[1]:02d})")
    if len(parts) > 2:
        try:
            datetime(year, parts[1], parts[2])
        except ValueError:
            raise ValueError(f"{field} is not a real date") from None
    return text


def _date_sort_key(text: str) -> tuple:
    """Pad a partial date so YYYY and YYYY-MM compare sensibly."""
    parts = [int(p) for p in text.split("-")] if text else []
    return tuple(parts + [0] * (3 - len(parts)))


def _linked_skill_ids(db: Session, user_id, raw) -> list[str]:
    """Keep only ids of skills that exist on this user's own profile.

    A project may reference a skill whether or not it has been verified yet —
    verification is what decides who gets to *see* the link, not whether it can
    be made.
    """
    if not isinstance(raw, list):
        raise ValueError("Skills must be a list")
    wanted, seen = [], set()
    for item in raw[:40]:
        text = str(item or "").strip()
        try:
            key = str(uuid.UUID(text))
        except (ValueError, AttributeError, TypeError):
            raise ValueError("Each linked skill must be a skill id")
        if key not in seen:
            seen.add(key)
            wanted.append(key)
    if not wanted:
        return []
    owned = {
        str(s.id)
        for s in db.execute(
            select(ProfileSkill).where(ProfileSkill.user_id == user_id)
        ).scalars()
    }
    missing = [k for k in wanted if k not in owned]
    if missing:
        raise ValueError("You can only link skills from your own profile")
    return wanted


def _apply_project_fields(
    db: Session, p: ProfileProject, body: dict, *, creating: bool
) -> None:
    if creating or "title" in body:
        title = (body.get("title") or "").strip()
        if not title:
            raise ValueError("A project title is required")
        p.title = title[:200]
    if "description" in body:
        p.description = str(body.get("description") or "").strip()[:5000]
    if "skill_ids" in body:
        p.skill_ids = _linked_skill_ids(db, p.user_id, body.get("skill_ids") or [])
    if "start_date" in body:
        p.start_date = _clean_date(body.get("start_date"), "Start date")
    if "end_date" in body:
        p.end_date = _clean_date(body.get("end_date"), "End date")
    if "present" in body:
        p.present = bool(body.get("present"))
    if p.present:
        p.end_date = ""
    if (
        p.start_date
        and p.end_date
        and _date_sort_key(p.end_date) < _date_sort_key(p.start_date)
    ):
        raise ValueError("End date can't be before the start date")
    if "repository_url" in body:
        p.repository_url = _clean_url(body.get("repository_url"), "Repository link")
    if "live_demo_url" in body:
        p.live_demo_url = _clean_url(body.get("live_demo_url"), "Live demo link")


def _clean_url(value, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not re.match(r"^https?://[^\s]+\.[^\s]+$", text, re.I):
        raise ValueError(f"{field} must be a valid http(s) URL")
    return text[:1000]


def create_project(db: Session, user_id, body: dict, requester) -> ProfileProject:
    if not access.can_edit_items(requester, user_id):
        raise PermissionError("You can't edit this profile")
    project = ProfileProject(user_id=user_id)
    _apply_project_fields(db, project, body, creating=True)
    db.add(project)
    db.flush()  # assign project.id before visibility is set
    _set_visibility(db, project, body.get("visibility"), requester)
    db.commit()
    return project


def update_project(
    db: Session, project: ProfileProject, body: dict, requester
) -> ProfileProject:
    if not access.can_edit_items(requester, project.user_id):
        raise PermissionError("You can't edit this profile")
    before = project.snapshot()
    _apply_project_fields(db, project, body, creating=False)
    if "visibility" in body:
        _set_visibility(db, project, body.get("visibility"), requester)
    if project.snapshot() != before:
        project.mark_dirty()
    db.commit()
    return project


def delete_project(db: Session, project: ProfileProject, requester) -> None:
    if not access.can_edit_items(requester, project.user_id):
        raise PermissionError("You can't edit this profile")
    _purge_verifications(db, "project", project.id)
    db.delete(project)
    db.commit()


# ---------------------------------------------------------------------------
# Certificates
# ---------------------------------------------------------------------------
def list_certificates(db: Session, user_id):
    return list(
        db.execute(
            select(ProfileCertificate)
            .where(ProfileCertificate.user_id == user_id)
            .order_by(ProfileCertificate.created_at.desc())
        ).scalars()
    )


def slugify(text: str) -> str:
    """Lower-case, hyphenated handle for a certificate title."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(text or "").strip().lower()).strip("-")
    return slug[:120]


def _unique_slug(db: Session, user_id, wanted: str, *, exclude_id=None) -> str:
    """``wanted``, suffixed with -2, -3 … until it is free on this profile.

    Slugs are per-profile, not global: two students may each have a
    "react-course" certificate and both keep the readable URL.
    """
    base = wanted or "certificate"
    candidate = base
    n = 1
    while True:
        stmt = select(ProfileCertificate).where(
            ProfileCertificate.user_id == user_id,
            ProfileCertificate.slug == candidate,
        )
        if exclude_id is not None:
            stmt = stmt.where(ProfileCertificate.id != exclude_id)
        if db.execute(stmt).scalar_one_or_none() is None:
            return candidate
        n += 1
        suffix = f"-{n}"
        candidate = f"{base[: 120 - len(suffix)]}{suffix}"


def _apply_certificate_fields(
    db: Session, cert: ProfileCertificate, body: dict, *, creating: bool
) -> None:
    if creating or "title" in body:
        title = str(body.get("title") or "").strip()
        if not title:
            raise ValueError("A certificate title is required")
        cert.title = title[:200]
    if creating or "slug" in body or "title" in body:
        # An explicit slug wins; otherwise it follows the title. Either way it
        # is normalised and de-duplicated before it reaches the column.
        raw = body.get("slug") if str(body.get("slug") or "").strip() else cert.title
        wanted = slugify(raw)
        if not wanted:
            raise ValueError("A certificate slug must contain letters or numbers")
        if wanted != cert.slug:
            cert.slug = _unique_slug(
                db, cert.user_id, wanted, exclude_id=None if creating else cert.id
            )


def _store_certificate_file(cert: ProfileCertificate, file) -> str:
    """Save the upload onto ``cert`` and return the path it replaced.

    Images only: a certificate is shown as a 16:9 preview wherever it appears,
    and a PDF has no thumbnail to show there.
    """
    previous = cert.file_path
    cert.file_path = save_attachment(
        file, "certificates", content_types=ATTACHMENT_CONTENT_TYPES
    )
    cert.file_name = (file.filename or "certificate")[:200]
    cert.file_type = (file.content_type or "").split(";")[0].strip().lower()[:100]
    try:
        cert.file_size = (settings.MEDIA_ROOT / cert.file_path).stat().st_size
    except OSError:
        cert.file_size = 0
    return previous


def create_certificate(
    db: Session, user_id, body: dict, file, requester
) -> ProfileCertificate:
    """Create a certificate. The file is mandatory — a certificate without its
    document is an empty claim, and there is no other content to review."""
    if not access.can_edit_items(requester, user_id):
        raise PermissionError("You can't edit this profile")
    if file is None:
        raise ValueError("A certificate file is required")
    cert = ProfileCertificate(user_id=user_id)
    _apply_certificate_fields(db, cert, body, creating=True)
    _store_certificate_file(cert, file)
    db.add(cert)
    db.flush()  # assign cert.id before visibility is set
    _set_visibility(db, cert, body.get("visibility"), requester)
    db.commit()
    return cert


def update_certificate(
    db: Session, cert: ProfileCertificate, body: dict, requester
) -> ProfileCertificate:
    if not access.can_edit_items(requester, cert.user_id):
        raise PermissionError("You can't edit this profile")
    before = cert.snapshot()
    _apply_certificate_fields(db, cert, body, creating=False)
    if "visibility" in body:
        _set_visibility(db, cert, body.get("visibility"), requester)
    if cert.snapshot() != before:
        cert.mark_dirty()
    db.commit()
    return cert


def set_certificate_file(
    db: Session, cert: ProfileCertificate, file, requester
) -> ProfileCertificate:
    """Replace the document. The old file is removed once the swap commits."""
    if not access.can_edit_items(requester, cert.user_id):
        raise PermissionError("You can't edit this profile")
    if file is None:
        raise ValueError("A file is required")
    before = cert.snapshot()
    previous = _store_certificate_file(cert, file)
    if cert.snapshot() != before:
        cert.mark_dirty()
    db.commit()
    if previous and previous != cert.file_path:
        delete_media(previous)
    return cert


def delete_certificate(db: Session, cert: ProfileCertificate, requester) -> None:
    if not access.can_edit_items(requester, cert.user_id):
        raise PermissionError("You can't edit this profile")
    path = cert.file_path
    _purge_verifications(db, "certificate", cert.id)
    db.delete(cert)
    db.commit()
    delete_media(path)


# ---------------------------------------------------------------------------
# Social / external accounts (AP-8)
# ---------------------------------------------------------------------------
# Spec §5.2 supports exactly GitHub, LinkedIn and Discord. Each has its own
# handle grammar; anything else is rejected rather than stored unvalidated.
_USERNAME_RULES = {
    SocialPlatform.GITHUB.value: (
        re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$"),
        "A GitHub username is 1–39 letters, digits or single hyphens.",
    ),
    SocialPlatform.LINKEDIN.value: (
        re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{2,99}$"),
        "A LinkedIn public profile id is 3–100 letters, digits or hyphens.",
    ),
    SocialPlatform.DISCORD.value: (
        re.compile(r"^(?!.*\.\.)[a-z0-9._]{2,32}$"),
        "A Discord username is 2–32 lowercase letters, digits, '.' or '_'.",
    ),
}

_URL_PREFIXES = {
    SocialPlatform.GITHUB.value: ("github.com/",),
    SocialPlatform.LINKEDIN.value: ("linkedin.com/in/", "www.linkedin.com/in/"),
    SocialPlatform.DISCORD.value: ("discord.com/users/", "discordapp.com/users/"),
}

_PROFILE_URLS = {
    SocialPlatform.GITHUB.value: "https://github.com/{u}",
    SocialPlatform.LINKEDIN.value: "https://www.linkedin.com/in/{u}",
    # Discord has no public profile page for a plain username — but
    # discord.com/users/{id} works for the numeric snowflake, which is what
    # hstaff's OAuth link gives us (see hstaff_sync.py), so link out when the
    # stored value looks like an id.
    SocialPlatform.DISCORD.value: "",
}


def _extract_username(platform: str, raw: str) -> str:
    """Accept either a bare handle or a full profile URL and normalise it."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("A username or profile link is required")
    text = text.lstrip("@")
    lowered = text.lower()
    if lowered.startswith(("http://", "https://")):
        stripped = re.sub(r"^https?://", "", text, flags=re.I)
        for prefix in _URL_PREFIXES[platform]:
            if stripped.lower().startswith(prefix):
                text = stripped[len(prefix) :]
                break
        else:
            raise ValueError("That link doesn't point at a supported profile")
    return text.strip("/").split("/")[0].split("?")[0]


def validate_account(platform: str, raw: str) -> tuple[str, str]:
    """Return ``(username, canonical_url)`` or raise with a readable reason."""
    if platform not in SOCIAL_PLATFORM_VALUES:
        raise ValueError("Only GitHub, LinkedIn and Discord are supported")
    username = _extract_username(platform, raw)
    pattern, message = _USERNAME_RULES[platform]
    is_discord = platform == SocialPlatform.DISCORD.value
    candidate = username.lower() if is_discord else username
    if not pattern.match(candidate):
        raise ValueError(message)
    username = candidate
    if is_discord and username.isdigit():
        return username, f"https://discord.com/users/{username}"
    template = _PROFILE_URLS[platform]
    return username, (template.format(u=username) if template else "")


def list_social_accounts(db: Session, user: User, viewer: User | None = None):
    """Anonymous (and other non-owner/curator) viewers just read what's
    already saved locally — no live hstaff call on every public page view.
    The owner and Academy Manager/Admin get a fresh pull from hstaff first,
    which overwrites the local copy so it never goes stale for the people who
    actually manage this profile."""
    if access.is_owner(viewer, user.id) or access.is_curator(viewer):
        _import_hstaff_socials(db, user)
    return list(
        db.execute(
            select(ProfileSocialAccount)
            .where(ProfileSocialAccount.user_id == user.id)
            .order_by(ProfileSocialAccount.platform)
        ).scalars()
    )


def _import_hstaff_socials(db: Session, user: User) -> None:
    """Refresh local social-account claims from hstaff's own profile fields.

    hstaff is the source of truth for the platforms it tracks: a platform
    missing locally gets created, and one that already exists gets
    overwritten when hstaff's value has changed — not just seeded once and
    left to drift.
    """
    existing = {
        a.platform: a
        for a in db.execute(
            select(ProfileSocialAccount).where(ProfileSocialAccount.user_id == user.id)
        ).scalars()
    }
    remote = hstaff_sync.fetch(db, user)
    changed = False
    for platform, raw in remote.items():
        try:
            username, url = validate_account(platform, raw)
        except ValueError:
            continue  # hstaff's value doesn't fit our stricter grammar; skip it
        account = existing.get(platform)
        if account is None:
            account = ProfileSocialAccount(
                user_id=user.id, platform=platform, username=username, url=url
            )
            db.add(account)
            db.flush()
            account.auto_verify()
            changed = True
        elif account.username != username:
            account.username = username
            account.url = url
            account.auto_verify()
            changed = True
    if changed:
        db.commit()


def create_social_account(db: Session, user_id, body: dict, requester):
    if not access.can_edit_items(requester, user_id):
        raise PermissionError("You can't edit this profile")
    platform = (body.get("platform") or "").strip()
    username, url = validate_account(platform, body.get("username"))
    existing = db.execute(
        select(ProfileSocialAccount).where(
            ProfileSocialAccount.user_id == user_id,
            ProfileSocialAccount.platform == platform,
        )
    ).scalar_one_or_none()
    if existing:
        raise ValueError(f"A {platform} account is already linked")
    account = ProfileSocialAccount(
        user_id=user_id, platform=platform, username=username, url=url
    )
    db.add(account)
    db.flush()
    _set_visibility(db, account, body.get("visibility"), requester)
    # No mentor review: the link is live the instant it's saved.
    account.auto_verify()
    db.commit()
    if str(requester.id) == str(user_id):
        hstaff_sync.push(db, requester, platform, username)
    return account


def update_social_account(db: Session, account: ProfileSocialAccount, body, requester):
    if not access.can_edit_items(requester, account.user_id):
        raise PermissionError("You can't edit this profile")
    before = account.snapshot()
    if "username" in body:
        username, url = validate_account(account.platform, body.get("username"))
        account.username = username
        account.url = url
    if "visibility" in body:
        _set_visibility(db, account, body.get("visibility"), requester)
    changed = account.snapshot() != before
    if changed:
        account.auto_verify()
    db.commit()
    if changed and "username" in body and str(requester.id) == str(account.user_id):
        hstaff_sync.push(db, requester, account.platform, account.username)
    return account


def delete_social_account(
    db: Session, account: ProfileSocialAccount, requester
) -> None:
    if not access.can_edit_items(requester, account.user_id):
        raise PermissionError("You can't edit this profile")
    _purge_verifications(db, "social_account", account.id)
    db.delete(account)
    db.commit()


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------
def _set_visibility(db: Session, item, value, requester) -> None:
    raw = (value or "").strip()
    if not raw:
        return
    if raw not in VISIBILITY_VALUES:
        raise ValueError("Unknown visibility level")
    if not access.can_set_visibility(requester, raw):
        raise PermissionError(
            "Only an Academy Manager can change who this profile item is visible to"
        )
    if raw == item.visibility:
        return
    item.visibility = raw


def override_visibility(
    db: Session, item_type: str, item, staff, *, visibility: str, reason: str
):
    """Academy Manager / Admin override of an item's public-section visibility.

    Distinct from an ordinary edit: this is the explicit staff-override
    action the ticket asks for, so — unlike ``_set_visibility`` above — a
    reason is mandatory and the owner is notified. HR is rejected here exactly
    as anywhere else that checks ``access.is_curator`` — CURATOR_ROLES never
    includes HR.
    """
    if not access.is_curator(staff):
        raise PermissionError(
            "Only an Academy Manager or Admin can override profile visibility"
        )
    value = (visibility or "").strip()
    if value not in VISIBILITY_VALUES:
        raise ValueError("Unknown visibility level")
    clean_reason = (reason or "").strip()
    if not clean_reason:
        raise ValueError("A reason is required to override visibility")
    if value == item.visibility:
        raise ValueError("That item already has this visibility level")

    previous = item.visibility
    item.visibility = value
    db.commit()

    owner = item.user or db.get(User, item.user_id)
    notifications.notify_owner_of_override(
        db,
        owner=owner,
        actor=staff,
        item_type=item_type,
        item_id=item.id,
        field="visibility",
        previous_value=previous,
        new_value=value,
        reason=clean_reason,
    )
    return item


# ---------------------------------------------------------------------------
# Share profile — per-user, per-section public exposure (shareProfile.md §6/§8)
# ---------------------------------------------------------------------------
# Sections backed by a reviewable item table. Curation is at the section
# level (spec: "never per item"), so when a section's audience level changes,
# every item in it adopts that same level — bio/languages/activity/analytics
# have no per-item model and are skipped.
_SECTION_ITEM_MODELS = {
    ProfileSection.SKILLS.value: ProfileSkill,
    ProfileSection.PROJECTS.value: ProfileProject,
    ProfileSection.EXTERNAL_ACCOUNTS.value: ProfileSocialAccount,
    ProfileSection.CERTIFICATIONS.value: ProfileCertificate,
}


def _cascade_section_visibility(db: Session, user_id, section: str, level: str) -> None:
    """Push a section's new audience level onto every item in it, so
    publishing a section actually makes its items visible instead of leaving
    them stuck on whatever ``visibility`` they defaulted to."""
    model = _SECTION_ITEM_MODELS.get(section)
    if model is None:
        return
    for item in db.execute(select(model).where(model.user_id == user_id)).scalars():
        item.visibility = level


def get_share(db: Session, user_id) -> ProfileShare:
    """Fetch (or lazily create) a user's share settings. Private by default."""
    share = db.execute(
        select(ProfileShare).where(ProfileShare.user_id == user_id)
    ).scalar_one_or_none()
    if share is None:
        share = ProfileShare(
            user_id=user_id,
            is_published=False,
            sections=dict(DEFAULT_SECTION_SHARING),
        )
        db.add(share)
        db.commit()
    return share


def _normalise_section_config(value) -> dict:
    """Coerce a legacy ``{section: bool}`` row or a fresh per-section config
    ``{section: {"visibility": ..., "include_unverified": bool}}`` into the
    canonical config dict."""
    if isinstance(value, dict):
        vis = (value.get("visibility") or "").strip()
        if vis not in VISIBILITY_VALUES:
            vis = Visibility.PRIVATE.value
        return {
            "visibility": vis,
            # "include_unverified": bool(value.get("include_unverified")),  #publish to share is not need unverified items, so we do not need include_unverified
        }
    # Legacy bool rows: exposed sections were fully public, hidden were private.
    return {
        "visibility": Visibility.PUBLIC.value if value else Visibility.PRIVATE.value,
        # "include_unverified": False,
    }


def _config_equal(a: dict, b: dict) -> bool:
    return a.get("visibility") == b.get("visibility")


# and bool(
#     a.get("include_unverified")
# ) == bool(b.get("include_unverified"))


def update_share(
    db: Session, user_id, requester, *, is_published=None, sections=None, reason=""
) -> ProfileShare:
    """Publish/unpublish a profile and set per-section visibility.

    Academy Manager / Admin only — the spec is explicit that no other role,
    including HR, may override this, and that the owner never controls it.

    ``sections`` is a partial ``{section: config}`` map merged over the
    current settings; each config may carry ``visibility`` (a
    :class:`app.profile.enums.Visibility` level, private when absent/invalid)
    and ``include_unverified`` (show never-approved items in the section).
    """
    if not access.is_curator(requester):
        raise PermissionError("Only an Academy Manager or Admin can share a profile")
    share = get_share(db, user_id)

    previous_published = share.is_published
    previous_sections = dict(share.sections or {})
    next_published = (
        bool(is_published) if is_published is not None else previous_published
    )
    next_sections = dict(previous_sections)

    if sections is not None:
        if not isinstance(sections, dict):
            raise ValueError("Sections must be an object of {section: config}")
        unknown = set(sections) - PROFILE_SECTION_VALUES
        if unknown:
            raise ValueError(f"Unknown section(s): {', '.join(sorted(unknown))}")
        for section, value in sections.items():
            next_sections[section] = _normalise_section_config(value)

    changes: list[dict] = []
    if next_published != previous_published:
        changes.append(
            {
                "field": "publish",
                "previous": "public" if previous_published else "private",
                "new": "public" if next_published else "private",
            }
        )
    for section in sorted(set(previous_sections) | set(next_sections)):
        old = _normalise_section_config(previous_sections.get(section))
        new = _normalise_section_config(next_sections.get(section))
        if not _config_equal(old, new):
            changes.append(
                {
                    "field": f"sections.{section}",
                    "previous": f"{old['visibility']}",
                    "new": f"{new['visibility']}",
                }
            )
            _cascade_section_visibility(db, user_id, section, new["visibility"])
            # if old["include_unverified"] != new["include_unverified"]:
            #    changes.append({
            #        "field": f"sections.{section}.include_unverified",
            #        "previous": str(old["include_unverified"]),
            #        "new": str(new["include_unverified"]),
            #    })

    clean_reason = (reason or "").strip()
    if changes and not clean_reason:
        raise ValueError("A reason is required for this override")

    share.is_published = next_published
    share.sections = next_sections
    share.updated_by_id = requester.id
    db.commit()

    if changes:
        if len(changes) == 1 and changes[0]["field"] == "publish":
            field = "publish"
        elif all(c["field"].startswith("sections.") for c in changes):
            field = "sections"
        else:
            field = "share"
        notifications.notify_owner_of_override(
            db,
            owner=share.user or db.get(User, share.user_id),
            actor=requester,
            item_type="share",
            item_id=share.user_id,
            field=field,
            previous_value=str(previous_published),
            new_value=str(next_published),
            reason=clean_reason,
            changes=changes,
        )
    return share


def _purge_verifications(db: Session, item_type: str, item_id) -> None:
    """Verification rows point at items by (type, id) rather than an FK, so
    they're cleaned up explicitly when the item goes away."""
    for record in db.execute(
        select(ProfileVerification).where(
            ProfileVerification.item_type == item_type,
            ProfileVerification.item_id == item_id,
        )
    ).scalars():
        db.delete(record)
