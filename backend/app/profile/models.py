"""Student profile records: skills, projects and external accounts.

These are *local* iClass tables — deliberately not the hstaff talent mirror.
hstaff stores a skill as a bare name with a pending/verified flag, which can't
carry the category, proficiency, evidence or version history
the profile spec requires.

The central idea is ``ReviewableMixin``: every user-provided profile item is a
*claim* that carries both a working copy (the columns themselves) and the last
approved copy (``approved_snapshot``). Editing a verified item mutates the
working copy and sends it back to review while ``approved_snapshot`` keeps
serving every audience that isn't the owner — that is what "the previously
approved version stays visible" means throughout the spec.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from app.core.database import BaseModel
from app.profile.enums import (
    ItemType,
    ReviewState,
    SkillLevel,
    VISIBILITY_VALUES,
    Visibility,
)


class ReviewableMixin:
    """Shared verification state for any profile item (spec §7).

    ``version`` counts every submission the owner makes; ``approved_version``
    records which of those the reviewer signed off on. When the two differ and
    ``approved_snapshot`` is set, the item has unreviewed edits sitting on top
    of an approved version.
    """

    review_state: Mapped[str] = mapped_column(
        String(20), default=ReviewState.DRAFT.value, index=True
    )
    # DEPRECATED (see enums.Visibility): superseded by per-section curation in
    # ProfileShare. Kept NOT NULL with a server default so existing rows and
    # fresh inserts both stay valid without a migration.
    visibility: Mapped[str] = mapped_column(
        String(20),
        default=Visibility.PRIVATE.value,
        server_default=Visibility.PRIVATE.value,
    )

    version: Mapped[int] = mapped_column(Integer, default=1)
    approved_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Content fields of the last approved version, as produced by ``snapshot()``.
    # Kept verbatim so an edit under review never destroys what was approved.
    approved_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verification_notes: Mapped[str] = mapped_column(Text, default="")
    rejection_reason: Mapped[str] = mapped_column(Text, default="")

    @declared_attr
    def user_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("users.id", ondelete="CASCADE"),
            index=True,
        )

    @declared_attr
    def reviewer_id(cls) -> Mapped[uuid.UUID | None]:
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        )

    @declared_attr
    def user(cls):
        return relationship("User", foreign_keys=[cls.user_id])

    @declared_attr
    def reviewer(cls):
        return relationship("User", foreign_keys=[cls.reviewer_id])

    # ---- helpers shared by services / serialisers ----
    @property
    def is_verified(self) -> bool:
        return self.review_state == ReviewState.VERIFIED.value

    @property
    def has_pending_changes(self) -> bool:
        """True when unreviewed edits sit on top of an already-approved version."""
        return bool(
            self.approved_snapshot is not None and self.approved_version != self.version
        )

    @property
    def is_pending_review(self) -> bool:
        return self.review_state in (
            ReviewState.SUBMITTED.value,
            ReviewState.UNDER_REVIEW.value,
        )

    def snapshot(self) -> dict:  # pragma: no cover - overridden by each model
        raise NotImplementedError

    def resolve_content(self, owner_view: bool) -> dict:
        """Pick the version this viewer gets: the owner's working copy, or the
        approved snapshot for everyone else.

        Used by ``profile/router.py`` when building every read-endpoint
        payload (skill/project/social-account/certificate list & detail
        views) — the owner previewing their own profile needs their live
        edits, while any other viewer (including the public share) must only
        ever see what a reviewer actually signed off on.

        Approved snapshots are frozen JSON, so one written before a field was
        retired still carries that key. Intersecting with the model's current
        snapshot keeps responses in step with the schema.
        """
        current = self.snapshot()
        approved = self.approved_snapshot
        if owner_view or approved is None:
            return current
        return {k: approved.get(k, v) for k, v in current.items()}

    def build_review_payload(self) -> dict:
        """Assemble the review-status block the owner sees on every item
        (AP-9) — spread into each item's owner-facing response alongside
        ``resolve_content`` in ``profile/router.py`` so the owner can see both
        the content and where it stands in the workflow."""
        return {
            "review_state": self.review_state,
            "visibility": self.visibility,
            "version": self.version,
            "approved_version": self.approved_version,
            "has_pending_changes": self.has_pending_changes,
            "is_verified": self.is_verified,
            "submitted_at": (
                self.submitted_at.isoformat() if self.submitted_at else None
            ),
            "verified_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "verified_by": (
                self.reviewer.full_name or self.reviewer.email
                if self.reviewer
                else None
            ),
            "verification_notes": self.verification_notes,
            "rejection_reason": self.rejection_reason,
        }

    def mark_dirty(self) -> None:
        """Call from ``profile/services.py`` right after mutating this item's
        content fields (e.g. ``update_skill``, ``update_project``,
        ``update_certificate``) — never on creation, and never for social
        accounts, which use ``auto_verify`` instead.

        A verified item that gets edited must go back to review, but its
        approved snapshot is deliberately left in place so readers keep seeing
        the approved version (spec §7 / AP-5 / AP-7).
        """
        if self.review_state in (
            ReviewState.SUBMITTED.value,
            ReviewState.UNDER_REVIEW.value,
            ReviewState.VERIFIED.value,
        ):
            self.version += 1
        self.review_state = ReviewState.DRAFT.value
        self.submitted_at = None
        # Reviewer metadata describes the *approved* version, so it only
        # survives while an approved version exists.
        if self.approved_snapshot is None:
            self.reviewer_id = None
            self.reviewed_at = None
            self.verification_notes = ""
        self.rejection_reason = ""

    def auto_verify(self) -> None:
        """Skip the review workflow entirely: the item is live the moment
        it's saved. Called from ``profile/services.py`` for social accounts
        on create/update/hstaff-sync, and for any item whose owner's role
        doesn't route to a reviewer (``submit_for_review`` when
        ``_review_tier`` returns ``None``) — a claim only mattered here as an
        ownership check, and hstaff (the account's actual owner) is the one
        propagating the change.
        """
        self.review_state = ReviewState.VERIFIED.value
        self.approved_snapshot = self.snapshot()
        self.approved_version = self.version
        self.submitted_at = None
        self.rejection_reason = ""


class ProfileSkill(BaseModel, ReviewableMixin):
    """A skill claim on a student's profile (spec §5.3, tickets AP-5 / AP-6)."""

    __tablename__ = "profile_skills"
    __table_args__ = (
        # One row per skill name per user; edits update the row rather than
        # stacking duplicates, which keeps the approved snapshot meaningful.
        UniqueConstraint("user_id", "name", name="uq_profile_skill_user_name"),
    )

    name: Mapped[str] = mapped_column(String(100), index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    level: Mapped[str] = mapped_column(String(20), default=SkillLevel.BEGINNER.value)

    # AP-6 detail fields.
    proficiency_note: Mapped[str] = mapped_column(Text, default="")
    related_technologies: Mapped[list] = mapped_column(JSONB, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")

    # Lower-cased ``name`` so search stays index-friendly and case-insensitive.
    search_key: Mapped[str] = mapped_column(String(100), default="", index=True)

    # Optional supporting file (image or PDF) — a certificate, a screenshot of
    # an assessment result. Stored under MEDIA_ROOT; the original filename is
    # kept only so the UI has something readable to show.
    attachment_path: Mapped[str] = mapped_column(String(300), default="")
    attachment_name: Mapped[str] = mapped_column(String(200), default="")

    def snapshot(self) -> dict:
        return {
            "name": self.name,
            "category": self.category,
            "level": self.level,
            "proficiency_note": self.proficiency_note,
            "related_technologies": list(self.related_technologies or []),
            "notes": self.notes,
            # Part of the content, not metadata: swapping the evidence out has
            # to send the claim back through review like any other edit.
            "attachment_path": self.attachment_path,
            "attachment_name": self.attachment_name,
        }


class ProfileProject(BaseModel, ReviewableMixin):
    """A portfolio project (spec §5.5, ticket AP-7)."""

    __tablename__ = "profile_projects"

    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    # Skills linked from the owner's own profile (ProfileSkill ids). Free-text
    # technologies were dropped so a project can only claim declared skills.
    skill_ids: Mapped[list] = mapped_column(JSONB, default=list)

    start_date: Mapped[str] = mapped_column(String(10), default="")
    end_date: Mapped[str] = mapped_column(String(10), default="")
    present: Mapped[bool] = mapped_column(Boolean, default=False)

    repository_url: Mapped[str] = mapped_column(Text, default="")
    live_demo_url: Mapped[str] = mapped_column(Text, default="")

    def snapshot(self) -> dict:
        return {
            "title": self.title,
            "description": self.description,
            "skill_ids": [str(s) for s in (self.skill_ids or [])],
            "start_date": self.start_date,
            "end_date": self.end_date,
            "present": self.present,
            "repository_url": self.repository_url,
            "live_demo_url": self.live_demo_url,
        }


class ProfileSocialAccount(BaseModel, ReviewableMixin):
    """A GitHub / LinkedIn / Discord account link (spec §5.2, ticket AP-8).

    Unlike skills, projects and certificates, this one skips mentor review —
    see ``ReviewableMixin.auto_verify``: it's saved already verified. It still
    carries ``ReviewableMixin`` for the visibility/audit-trail plumbing every
    item type shares, and it stays synced with hstaff's own
    github_username/linkedin_username/discord_username fields on the talent
    profile (see ``app.profile.hstaff_sync``).
    """

    __tablename__ = "profile_social_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "platform", name="uq_profile_social_user_platform"),
    )

    platform: Mapped[str] = mapped_column(String(20), index=True)
    username: Mapped[str] = mapped_column(String(120))
    # Canonical profile URL derived from platform + username on save.
    url: Mapped[str] = mapped_column(Text, default="")

    def snapshot(self) -> dict:
        return {
            "platform": self.platform,
            "username": self.username,
            "url": self.url,
        }


class ProfileCertificate(BaseModel, ReviewableMixin):
    """An uploaded certificate — a diploma, course completion or award.

    Deliberately thin: the *file* is the claim. A title (and the slug derived
    from it) is all the metadata there is, so the section stays a wall of
    downloadable documents rather than another form to fill in.

    Runs through the same verification workflow as skills and projects, and is
    read through the same visibility rules, so who may download a certificate
    is decided by exactly the machinery that decides who may see a skill.
    """

    __tablename__ = "profile_certificates"
    __table_args__ = (
        # The slug addresses the certificate in URLs, so it has to be unique
        # per profile; ``services._unique_slug`` suffixes collisions.
        UniqueConstraint("user_id", "slug", name="uq_profile_certificate_user_slug"),
    )

    title: Mapped[str] = mapped_column(String(200))
    # URL-safe handle, derived from the title unless the owner supplies one.
    slug: Mapped[str] = mapped_column(String(120), index=True)

    # The document itself: an image or a PDF under MEDIA_ROOT. ``file_name`` is
    # the original upload name, kept so a download arrives with the name the
    # owner recognises rather than the stored uuid.
    file_path: Mapped[str] = mapped_column(String(300), default="")
    file_name: Mapped[str] = mapped_column(String(200), default="")
    file_type: Mapped[str] = mapped_column(String(100), default="")
    file_size: Mapped[int] = mapped_column(Integer, default=0)

    def snapshot(self) -> dict:
        return {
            "title": self.title,
            "slug": self.slug,
            # The file is the content of the claim: replacing it has to send
            # the certificate back through review like any other edit.
            "file_path": self.file_path,
            "file_name": self.file_name,
            "file_type": self.file_type,
            "file_size": self.file_size,
        }


class ProfileShare(BaseModel):
    """Per-user public-profile sharing (shareProfile.md §6 + §8, v2 visibility).

    Two things, both owned by the Academy Manager / Admin — the profile owner
    controls neither (§6: "The profile owner cannot publish their own profile";
    §8: "The profile owner does not control public display"):

    * ``is_published`` — whether anonymous visitors may see the profile at all.
    * ``sections`` — per-section visibility config, as
      ``{section: {"visibility": <Visibility>, "include_unverified": bool}}``.
      Legacy rows store ``{section: bool}``; the accessor methods normalise
      those (an exposed section meant fully public, a hidden one means
      private). Absent keys are treated as private.

    ``include_unverified`` is the v2 "show verified or unverified items when
    explicitly chosen" flag: when true, viewers (including the public, once
    published) also see items that have never cleared review — rejected ones
    excepted.

    Logged-in users are unaffected by the public half of this: they see the
    profile per the section visibility level (the audience rule), which is
    what "internal by default" means here.
    """

    __tablename__ = "profile_shares"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    sections: Mapped[dict] = mapped_column(JSONB, default=dict)

    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    user = relationship("User", foreign_keys=[user_id])
    updated_by = relationship("User", foreign_keys=[updated_by_id])

    def _section_config(self, section: str) -> dict:
        raw = (self.sections or {}).get(section)
        if isinstance(raw, dict):
            vis = (raw.get("visibility") or "").strip()
            if vis not in VISIBILITY_VALUES:
                vis = Visibility.PRIVATE.value
            return {
                "visibility": vis,
                # "include_unverified": bool(raw.get("include_unverified")),
            }
        # Legacy bool rows: exposed sections were fully public, hidden were private.
        return {
            "visibility": (
                Visibility.PUBLIC.value if raw else Visibility.PRIVATE.value
            ),
            # "include_unverified": False,
        }

    def section_visibility(self, section: str) -> str:
        """This section's audience level, normalized from legacy bool rows."""
        return self._section_config(section)["visibility"]

    # def include_unverified(self, section: str) -> bool:
    #     """Whether viewers may see never-approved items in this section."""
    #     return self._section_config(section)["include_unverified"]

    def shows(self, section: str) -> bool:
        """Whether anonymous visitors may see ``section`` right now: the profile
        is published and the section is at a public audience level."""
        if not self.is_published:
            return False
        return self.section_visibility(section) in {
            Visibility.PUBLIC.value,
            # Visibility.PUBLIC_SUMMARY.value,
        }


class ProfileVerification(BaseModel):
    """One trip through the review workflow for one version of one item.

    This is the durable verification record the spec asks for (§7: verifier,
    date, level, evidence, notes, version) and doubles as the item's history —
    every submission appends a row, so an approved version's evidence survives
    later edits.
    """

    __tablename__ = "profile_verifications"

    item_type: Mapped[str] = mapped_column(String(20), index=True)
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    version: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(
        String(20), default=ReviewState.SUBMITTED.value, index=True
    )
    # The item's content at submission time, so a reviewer always sees exactly
    # what was submitted even if the owner edits again while it's queued.
    snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)

    # Evidence entries: {"kind": EvidenceKind, "reference": str, "note": str}.
    evidence: Mapped[list] = mapped_column(JSONB, default=list)

    # The mentor this submission was routed to, resolved from hstaff at submit
    # time (see profile/mentorship.py). Distinct from ``reviewer_id``, which
    # records who actually acted. NULL means hstaff had no mentor on file (or
    # the lookup failed), in which case the submission falls back to the shared
    # queue so it is never stranded.
    assigned_mentor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # True when the submitting student's tier routes this to the academy-manager
    # queue as well as the mentor (school / softlanding / foundation /
    # fundamental). Senior tiers (talent / growth3) are mentor-only and keep
    # this False; admin sees everything either way.
    academy_manager_review: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notes: Mapped[str] = mapped_column(Text, default="")
    rejection_reason: Mapped[str] = mapped_column(Text, default="")

    user = relationship("User", foreign_keys=[user_id])
    reviewer = relationship("User", foreign_keys=[reviewer_id])
    assigned_mentor = relationship("User", foreign_keys=[assigned_mentor_id])


# Maps the workflow discriminator onto the concrete tables.
ITEM_MODELS = {
    ItemType.SKILL.value: ProfileSkill,
    ItemType.PROJECT.value: ProfileProject,
    ItemType.SOCIAL_ACCOUNT.value: ProfileSocialAccount,
    ItemType.CERTIFICATE.value: ProfileCertificate,
}
