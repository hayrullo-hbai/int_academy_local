"""Populate Tolibjon's student profile with skills, projects and external
accounts so the profile pages have realistic content to render.

Most items are pushed all the way through the review workflow (submitted, then
approved by the superadmin) so they show up verified; a couple are deliberately
left in draft / awaiting-review so the pending states are visible too.

Idempotent: items that already exist on the profile are skipped, so re-running
only fills in what is missing.

Run:  docker compose exec backend python -m scripts.seed_tolibjon_profile
"""

import sys

from sqlalchemy import select

from app.core.database import SessionLocal
from app.identity.models import User
from app.profile import services
from app.profile.enums import ReviewState

TARGET_EMAIL = "tolibjon@example.com"
REVIEWER_EMAIL = "admin@academy.local"

# name, category, level, proficiency, related tech, workflow state
SKILLS = [
    (
        "Python",
        "programming_languages",
        "advanced",
        "Primary language for backend services and data tooling.",
        ["FastAPI", "SQLAlchemy", "pytest"],
        ReviewState.VERIFIED,
    ),
    (
        "TypeScript",
        "programming_languages",
        "advanced",
        "Types every front-end surface, including the profile module.",
        ["React", "Next.js", "Zod"],
        ReviewState.VERIFIED,
    ),
    (
        "FastAPI",
        "frameworks",
        "advanced",
        "Built the academy platform's REST layer, auth and RBAC.",
        ["Pydantic", "Starlette", "OAuth2"],
        ReviewState.VERIFIED,
    ),
    (
        "Next.js",
        "frameworks",
        "intermediate",
        "App-router pages, server components and route handlers.",
        ["React", "Tailwind CSS"],
        ReviewState.VERIFIED,
    ),
    (
        "PostgreSQL",
        "databases",
        "intermediate",
        "Schema design, JSONB columns and query tuning for the profile tables.",
        ["SQLAlchemy", "Alembic"],
        ReviewState.VERIFIED,
    ),
    (
        "Docker",
        "devops",
        "intermediate",
        "Containerised the whole stack and the local compose workflow.",
        ["Docker Compose", "GitHub Actions"],
        ReviewState.SUBMITTED,
    ),
    (
        "Mentoring",
        "soft_skills",
        "advanced",
        "Reviews mentee submissions and runs weekly pairing sessions.",
        [],
        ReviewState.VERIFIED,
    ),
    (
        "Kubernetes",
        "devops",
        "beginner",
        "Learning deployments and services on a home cluster.",
        ["Helm"],
        ReviewState.DRAFT,
    ),
]

# title, description, skills to link, start, end, present, repo, demo, state
PROJECTS = [
    (
        "Academy Profile Platform",
        "Student profile module with a full claim-and-verify workflow: skills, "
        "projects and external accounts each carry a working copy plus the last "
        "approved snapshot, so an edit never removes what a mentor already signed off.",
        ["Python", "FastAPI", "PostgreSQL", "TypeScript", "Next.js"],
        "2025-02",
        "",
        True,
        "https://github.com/tolibjon/academy-profile",
        "",
        ReviewState.VERIFIED,
    ),
    (
        "Onboarding Pipeline Tracker",
        "Six-stage hiring pipeline with per-stage reports and analytics dashboards "
        "for the recruitment team.",
        ["Python", "FastAPI", "PostgreSQL"],
        "2024-09",
        "2025-01",
        False,
        "https://github.com/tolibjon/onboarding-tracker",
        "",
        ReviewState.VERIFIED,
    ),
    (
        "Mentor Review Queue",
        "Routing layer that pins each submission to the student's own mentor at "
        "submit time and falls back to a shared queue when none is on file.",
        ["Python", "TypeScript"],
        "2025-05",
        "",
        True,
        "",
        "",
        ReviewState.SUBMITTED,
    ),
]

ACCOUNTS = [
    ("github", "tolibjon", ReviewState.VERIFIED),
    ("linkedin", "tolibjon-tadjiev", ReviewState.VERIFIED),
    ("discord", "tolibjon", ReviewState.DRAFT),
]


def advance(db, item_type, item, owner, reviewer, target: ReviewState) -> None:
    """Push a freshly created item to the requested workflow state."""
    if target is ReviewState.DRAFT:
        return
    services.submit_for_review(db, item_type, item, owner)
    if target is ReviewState.VERIFIED:
        services.approve(db, item_type, item, reviewer, notes="Seeded demo data.")


def main() -> int:
    db = SessionLocal()
    try:
        owner = db.execute(
            select(User).where(User.email == TARGET_EMAIL)
        ).scalar_one_or_none()
        reviewer = db.execute(
            select(User).where(User.email == REVIEWER_EMAIL)
        ).scalar_one_or_none()
        if owner is None or reviewer is None:
            print(f"Missing {TARGET_EMAIL!r} or {REVIEWER_EMAIL!r}")
            return 1

        existing_skills = {s.name: s for s in services.list_skills(db, owner.id)}
        for name, category, level, note, tech, state in SKILLS:
            if name in existing_skills:
                print(f"skill  · {name} (exists)")
                continue
            skill = services.create_skill(
                db,
                owner.id,
                {
                    "name": name,
                    "category": category,
                    "level": level,
                    "proficiency_note": note,
                    "related_technologies": tech,
                },
                owner,
            )
            existing_skills[name] = skill
            advance(db, "skill", skill, owner, reviewer, state)
            print(f"skill  + {name} → {skill.review_state}")

        existing_projects = {p.title for p in services.list_projects(db, owner.id)}
        for (
            title,
            desc,
            skill_names,
            start,
            end,
            present,
            repo,
            demo,
            state,
        ) in PROJECTS:
            if title in existing_projects:
                print(f"project· {title} (exists)")
                continue
            project = services.create_project(
                db,
                owner.id,
                {
                    "title": title,
                    "description": desc,
                    # Projects may only claim skills already on the profile.
                    "skill_ids": [
                        str(existing_skills[n].id)
                        for n in skill_names
                        if n in existing_skills
                    ],
                    "start_date": start,
                    "end_date": end,
                    "present": present,
                    "repository_url": repo,
                    "live_demo_url": demo,
                },
                owner,
            )
            advance(db, "project", project, owner, reviewer, state)
            print(f"project+ {title} → {project.review_state}")

        existing_accounts = {
            a.platform for a in services.list_social_accounts(db, owner.id)
        }
        for platform, username, state in ACCOUNTS:
            if platform in existing_accounts:
                print(f"account· {platform} (exists)")
                continue
            account = services.create_social_account(
                db, owner.id, {"platform": platform, "username": username}, owner
            )
            advance(db, "social_account", account, owner, reviewer, state)
            print(f"account+ {platform} → {account.review_state}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
