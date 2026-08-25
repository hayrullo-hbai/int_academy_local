"""Vocabularies for the student profile module (skills, projects, links).

Values follow the User Profile spec (docs: userProfile.md) rather than the
ticket summaries wherever the two disagree — see §5.3 (skill categories),
§7 (verification workflow) and §10 (visibility).
"""

import enum


class ReviewState(str, enum.Enum):
    """Shared verification workflow (spec §7). Applies to every reviewable
    profile item: skills, projects and external accounts."""

    DRAFT = "draft"
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    VERIFIED = "verified"
    REJECTED = "rejected"


class Visibility(str, enum.Enum):
    """Per-item audience levels (spec §10) — also the per-section audience
    levels of the v2 visibility model (ProfileShare.sections).

    Owners may pick any in-platform level (``OWNER_VISIBILITY_VALUES``); the
    public levels (``PUBLIC_VISIBILITY_VALUES``) are reserved for the Academy
    Manager / Admin. Per-item values gate item access (``access.can_view_item``)
    and per-section values gate whole sections (``access.can_view_section``).
    """

    PRIVATE = "private"
    # MENTOR_ONLY = "mentor_only"
    # HR_ONLY = "hr_only"
    # ACADEMY_ONLY = "academy_only"
    # PUBLIC_SUMMARY = "public_summary"
    PUBLIC = "public"


VISIBILITY_VALUES = {v.value for v in Visibility}

# profile owners can choose these values for privacy when they have the right to set visibility, but they cannot choose PUBLIC or PUBLIC_SUMMARY
OWNER_VISIBILITY_VALUES = {
    Visibility.PRIVATE.value,
    # Visibility.MENTOR_ONLY.value,
    # Visibility.HR_ONLY.value,
    # Visibility.ACADEMY_ONLY.value,
}

# values for public visibility that can be set by Academy Manager or Admin, but not by profile owners
PUBLIC_VISIBILITY_VALUES = {
    Visibility.PUBLIC.value,
    # Visibility.PUBLIC_SUMMARY.value,
}


class SkillCategory(str, enum.Enum):
    """Spec §5.3 — the profile skill categories."""

    PROGRAMMING_LANGUAGES = "programming_languages"
    FRAMEWORKS = "frameworks"
    DATABASES = "databases"
    DEVOPS = "devops"
    MACHINE_LEARNING = "machine_learning"
    CLOUD = "cloud"
    MOBILE = "mobile"
    UI_UX = "ui_ux"
    SOFT_SKILLS = "soft_skills"
    LEADERSHIP = "leadership"


SKILL_CATEGORY_VALUES = {c.value for c in SkillCategory}

SKILL_CATEGORY_LABELS = {
    SkillCategory.PROGRAMMING_LANGUAGES.value: "Programming Languages",
    SkillCategory.FRAMEWORKS.value: "Frameworks",
    SkillCategory.DATABASES.value: "Databases",
    SkillCategory.DEVOPS.value: "DevOps",
    SkillCategory.MACHINE_LEARNING.value: "Machine Learning",
    SkillCategory.CLOUD.value: "Cloud",
    SkillCategory.MOBILE.value: "Mobile",
    SkillCategory.UI_UX.value: "UI/UX",
    SkillCategory.SOFT_SKILLS.value: "Soft Skills",
    SkillCategory.LEADERSHIP.value: "Leadership",
}


class SkillLevel(str, enum.Enum):
    """Owner-declared proficiency."""

    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


SKILL_LEVEL_VALUES = {lv.value for lv in SkillLevel}


class EvidenceKind(str, enum.Enum):
    """Supporting evidence accepted for review (spec §8)."""

    # Skills
    ASSESSMENT_RESULT = "assessment_result"
    MENTOR_OBSERVATION = "mentor_observation"
    TECHNICAL_INTERVIEW = "technical_interview"
    PROJECT_CONTRIBUTION = "project_contribution"
    # Projects
    GITHUB_REPOSITORY = "github_repository"
    PULL_REQUEST = "pull_request"
    JIRA = "jira"
    ACADEMY_ASSIGNMENT = "academy_assignment"
    PM_CONFIRMATION = "pm_confirmation"
    LIVE_DEMO = "live_demo"


EVIDENCE_KIND_VALUES = {e.value for e in EvidenceKind}


class SocialPlatform(str, enum.Enum):
    """Spec §5.2 — the only supported external account platforms."""

    GITHUB = "github"
    LINKEDIN = "linkedin"
    DISCORD = "discord"


SOCIAL_PLATFORM_VALUES = {p.value for p in SocialPlatform}


class ItemType(str, enum.Enum):
    """Discriminator for the shared verification workflow (spec §7 applies to
    "Skills, Projects, Portfolio claims")."""

    SKILL = "skill"
    PROJECT = "project"
    SOCIAL_ACCOUNT = "social_account"
    CERTIFICATE = "certificate"


ITEM_TYPE_VALUES = {t.value for t in ItemType}


class ProfileSection(str, enum.Enum):
    """Curatable sections of the public profile (shareProfile.md §8).

    Curation is at the SECTION level, never per item: an Academy Manager
    chooses which sections of a given user's profile are exposed publicly.
    """

    BIO = "bio"
    SKILLS = "skills"
    PROJECTS = "projects"
    LANGUAGES = "languages"
    CERTIFICATIONS = "certifications"
    ACTIVITY = "activity"
    ANALYTICS = "analytics"
    EXTERNAL_ACCOUNTS = "external_accounts"


PROFILE_SECTION_VALUES = {s.value for s in ProfileSection}

PROFILE_SECTION_LABELS = {
    ProfileSection.BIO.value: "Bio",
    ProfileSection.SKILLS.value: "Skills",
    ProfileSection.PROJECTS.value: "Projects",
    ProfileSection.LANGUAGES.value: "Languages",
    ProfileSection.CERTIFICATIONS.value: "Certifications",
    ProfileSection.ACTIVITY.value: "Activity",
    ProfileSection.ANALYTICS.value: "Analytics",
    ProfileSection.EXTERNAL_ACCOUNTS.value: "External Accounts",
}

# shareProfile.md §6: "A profile is Private by default." Nothing is exposed
# publicly until an Academy Manager both publishes it and opts a section in.
DEFAULT_SECTION_SHARING = {s: False for s in sorted(PROFILE_SECTION_VALUES)}
