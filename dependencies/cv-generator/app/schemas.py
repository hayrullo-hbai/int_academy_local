from typing import Any, Literal

from pydantic import BaseModel, Field


class UserPayload(BaseModel):
    """Minimal user identity block supplied by the backend."""

    id: str = ""
    email: str = ""
    full_name: str = ""
    phone: str = ""
    hstaff_profile: dict[str, Any] = Field(default_factory=dict)
    academy_progress_public: bool = False
    public_profile_url: str = ""


class SkillPayload(BaseModel):
    name: str
    level: str = ""
    category: str = ""
    # hstaff-mirror path uses ``verified``; profile path uses ``review_state``.
    verified: bool = True
    review_state: str = ""
    approved_snapshot: dict[str, Any] | None = None


class ProjectPayload(BaseModel):
    id: str = ""
    title: str
    role: str = ""
    urls: list[str] = Field(default_factory=list)
    start_date: str = ""
    end_date: str = ""
    present: bool = False
    bullets: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    description: str = ""
    # Visibility / verification fields used by the allow-list builder.
    verified: bool = True
    hidden: bool = False
    confidential: bool = False
    review_state: str = ""
    approved_snapshot: dict[str, Any] | None = None
    confidentiality: str = ""
    public_summary_approved: bool = False
    public_summary: str = ""
    repository_url: str = ""
    live_demo_url: str = ""
    technologies: list[str] = Field(default_factory=list)


class ExperiencePayload(BaseModel):
    id: str = ""
    company: str = ""
    role: str = ""
    start_date: str = ""
    end_date: str = ""
    present: bool = False
    description: str = ""
    technologies: list[str] = Field(default_factory=list)
    company_logo_url: str = ""
    verified: bool = True
    hidden: bool = False


class CertificatePayload(BaseModel):
    title: str
    issuer: str = ""
    issue_date: str = ""
    verified: bool = True
    hidden: bool = False


class LanguagePayload(BaseModel):
    name: str
    level: str = ""


class SocialAccountPayload(BaseModel):
    """Verified profile social account."""

    platform: str = ""
    url: str = ""
    username: str = ""
    review_state: str = ""
    approved_snapshot: dict[str, Any] | None = None


class ProfileSharePayload(BaseModel):
    """Public profile curation state."""

    is_published: bool = False
    sections: dict[str, bool] = Field(default_factory=dict)


class TalentPayload(BaseModel):
    """Raw hstaff-mirror data (used when source='hstaff')."""

    hstaff_profile: dict[str, Any] = Field(default_factory=dict)
    skills: list[SkillPayload] = Field(default_factory=list)
    projects: list[ProjectPayload] = Field(default_factory=list)
    languages: list[LanguagePayload] = Field(default_factory=list)
    experience: list[ExperiencePayload] = Field(default_factory=list)
    certificates: list[CertificatePayload] = Field(default_factory=list)


class ProfilePayload(BaseModel):
    """Raw profile-workflow data (used when source='profile')."""

    hstaff_profile: dict[str, Any] = Field(default_factory=dict)
    skills: list[SkillPayload] = Field(default_factory=list)
    projects: list[ProjectPayload] = Field(default_factory=list)
    languages: list[LanguagePayload] = Field(default_factory=list)
    experience: list[ExperiencePayload] = Field(default_factory=list)
    certificates: list[CertificatePayload] = Field(default_factory=list)
    social_accounts: list[SocialAccountPayload] = Field(default_factory=list)
    share: ProfileSharePayload = Field(default_factory=ProfileSharePayload)


class CVPrefsPayload(BaseModel):
    target_role: str = ""
    hidden_sections: list[str] = Field(default_factory=list)
    project_order: list[str] = Field(default_factory=list)


class CVGenerateRequest(BaseModel):
    """Request from the backend to render a CV.

    When ``snapshot`` is omitted the service builds it from ``user`` +
    ``talent``/``profile``; otherwise the supplied snapshot is rendered directly.
    """

    source: Literal["profile", "hstaff"] = "profile"
    target_role: str
    public: bool = False
    user: UserPayload = Field(default_factory=UserPayload)
    talent: TalentPayload = Field(default_factory=TalentPayload)
    profile: ProfilePayload = Field(default_factory=ProfilePayload)
    prefs: CVPrefsPayload | None = None
    snapshot: dict[str, Any] | None = None


class CVGenerateResponse(BaseModel):
    html: str
    snapshot: dict[str, Any]
    sha256: str
