"""Talent Profile CV data model.

New tables only — all registered on ``Base.metadata`` via the import at the
bottom of ``app/main.py`` and auto-created by ``create_all`` at startup.

The local ``talent_*`` tables mirror the allow-listed slice of a talent's hstaff
profile (see ``services.sync_from_hstaff``). ``talent_cv`` holds generated CV
history and ``talent_cv_prefs`` the per target-role section/project layout prefs.
"""

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import BaseModel

# Verification state values (draft → pending → approved/rejected, or verified).
SKILL_STATUS_VALUES = {
    "draft",
    "pending",
    "approved",
    "rejected",
    "verified",
}


class TalentSkill(BaseModel):
    __tablename__ = "talent_skills"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    level: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(16), default="pending")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    category: Mapped[str] = mapped_column(String(80), default="")
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    # Optional upstream (hstaff) id — set when the source item carries one. Rows
    # without a source id are treated as locally managed and never pruned by sync.
    source_id: Mapped[str] = mapped_column(String(64), default="", index=True)


class TalentLanguage(BaseModel):
    __tablename__ = "talent_languages"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(80))
    level: Mapped[str] = mapped_column(String(40), default="")
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    source_id: Mapped[str] = mapped_column(String(64), default="", index=True)


class TalentProject(BaseModel):
    __tablename__ = "talent_projects"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    urls: Mapped[list] = mapped_column(JSONB, default=list)
    start_date: Mapped[str] = mapped_column(String(32), default="")
    end_date: Mapped[str] = mapped_column(String(32), default="")
    present: Mapped[bool] = mapped_column(Boolean, default=False)
    bullets: Mapped[list] = mapped_column(JSONB, default=list)
    skills: Mapped[list] = mapped_column(JSONB, default=list)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    confidential: Mapped[bool] = mapped_column(Boolean, default=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    source_id: Mapped[str] = mapped_column(String(64), default="", index=True)


class TalentExperience(BaseModel):
    __tablename__ = "talent_experience"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    company: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[str] = mapped_column(String(255), default="")
    start_date: Mapped[str] = mapped_column(String(32), default="")
    end_date: Mapped[str] = mapped_column(String(32), default="")
    present: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str] = mapped_column(Text, default="")
    technologies: Mapped[list] = mapped_column(JSONB, default=list)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    source_id: Mapped[str] = mapped_column(String(64), default="", index=True)


class TalentCertificate(BaseModel):
    __tablename__ = "talent_certificates"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255), default="")
    issuer: Mapped[str] = mapped_column(String(255), default="")
    issue_date: Mapped[str] = mapped_column(String(32), default="")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    source_id: Mapped[str] = mapped_column(String(64), default="", index=True)


class TalentCV(BaseModel):
    """Generated CV history row: the allow-listed snapshot + its rendered HTML.

    ``sha256`` is over the canonical JSON of ``snapshot`` so callers can detect
    that a role's CV is unchanged without re-rendering (see ``services``).
    """

    __tablename__ = "talent_cv"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    target_role: Mapped[str] = mapped_column(String(64), index=True)
    snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    html: Mapped[str] = mapped_column(Text, default="")
    sha256: Mapped[str] = mapped_column(String(64), default="", index=True)


class TalentCVPrefs(BaseModel):
    """Per target-role CV preferences. Unique on ``(user_id, target_role)``.

    ``hidden_sections`` is a JSON list of section keys the owner wants excluded
    from their generated CV; ``project_order`` reorders the CV's project list by
    project id.
    """

    __tablename__ = "talent_cv_prefs"
    __table_args__ = (UniqueConstraint("user_id", "target_role"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    target_role: Mapped[str] = mapped_column(String(64))
    hidden_sections: Mapped[list] = mapped_column(JSONB, default=list)
    project_order: Mapped[list] = mapped_column(JSONB, default=list)
