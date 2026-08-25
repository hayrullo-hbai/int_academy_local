#!/usr/bin/env python3
"""Seed realistic mock talent data for a single member so we can generate + inspect a CV.

Directly inserts (and replaces) the member's talent_* rows in PostgreSQL, and
sets the account so it can log in locally (source=local + a known password) —
handy when the external hstaff auth endpoint is unreachable for manual testing.

Idempotent: it deletes the member's existing talent rows first, then re-inserts.
Run from the backend repo root:
    DATABASE_URL=postgresql://academy:academy@localhost:5433/academy_db \
        python scripts/seed_cv_mock.py [--email hayrullo.hbai@gmail.com] [--password admin12345]
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import datetime, timezone

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# dotenv's load_dotenv only fills *missing* env vars, so this default wins over
# the docker `db` host and lets the script reach the host-postgres (port 5433).
os.environ.setdefault(
    "DATABASE_URL", "postgresql://academy:academy@localhost:5433/academy_db"
)

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.identity.enums import UserSource, UserStatus  # noqa: E402
from app.identity.models import User  # noqa: E402
from app.profile.enums import ReviewState, Visibility  # noqa: E402
from app.profile.models import ProfileProject, ProfileSkill  # noqa: E402
from app.talent import models as tm  # noqa: E402


def _find_or_create_user(db: Session, email: str, password: str) -> tuple[User, bool]:
    """Reuse the existing account (exact, then slug-prefix) or create a local one.

    Returns ``(user, created)``.
    """
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        # The profile page resolves by slug prefix (hayrullo.hbai -> hayrullo.hbai@%),
        # so match that too and reuse the existing hstaff mirror instead of
        # creating a duplicate row (which would make the prefix match ambiguous).
        user = (
            db.execute(select(User).where(User.email.ilike(f"{email}@%")))
            .scalars()
            .first()
        )
    created = user is None
    if user is None:
        user = User(
            email=email,
            full_name="Hayrullo Abdubaniev",
            source=UserSource.LOCAL.value,
            is_active=True,
            status=UserStatus.ACTIVE.value,
            time_zone="Asia/Tashkent",
            office_location="tashkent",
        )
        db.add(user)
        db.flush()

    # Force local auth + a known password for offline testing. Keep the name and
    # hstaff_profile, but pin a bio so the CV summary has content.
    user.source = UserSource.LOCAL.value
    user.password_hash = hash_password(password)
    user.is_superuser = False
    user.is_active = True
    user.status = UserStatus.ACTIVE.value
    if not user.full_name:
        user.full_name = "Hayrullo Abdubaniev"
    hp = dict(user.hstaff_profile or {})
    hp["bio"] = (
        "Results-driven software engineer with 5+ years of experience designing, "
        "building and scaling backend systems and full-stack products. Passionate about "
        "clean architecture, developer productivity, and delivering measurable business impact. "
        "Led platform modernization initiatives that reduced infrastructure costs by 35% and "
        "improved API response times by 60%."
    )
    user.hstaff_profile = hp
    db.flush()
    return user, created


def _clear_talent(db: Session, user_id: uuid.UUID) -> None:
    for model in (
        tm.TalentCV,
        tm.TalentCVPrefs,
        tm.TalentProject,
        tm.TalentExperience,
        tm.TalentCertificate,
        tm.TalentSkill,
        tm.TalentLanguage,
    ):
        db.query(model).filter(model.user_id == user_id).delete()


def _clear_profile(db: Session, user_id: uuid.UUID) -> None:
    """Remove existing profile skills/projects so seeding is idempotent."""
    for model in (ProfileProject, ProfileSkill):
        db.query(model).filter(model.user_id == user_id).delete()


def _seed_profile(db: Session, user_id: uuid.UUID) -> dict:
    """Seed verified profile skills and public projects for the default CV path.

    The default ``source=profile`` CV builder reads skills/projects from the
    local profile workflow, so we need approved, verified rows there.
    """
    counts = {"profile_skills": 0, "profile_projects": 0}

    skill_specs = [
        # (name, level, category)
        ("Python", "expert", "programming_languages"),
        ("TypeScript", "advanced", "programming_languages"),
        ("SQL", "advanced", "programming_languages"),
        ("FastAPI", "expert", "frameworks"),
        ("Django", "advanced", "frameworks"),
        ("React", "intermediate", "frameworks"),
        ("PostgreSQL", "advanced", "databases"),
        ("Redis", "intermediate", "databases"),
        ("MongoDB", "intermediate", "databases"),
        ("Docker", "advanced", "devops"),
        ("Kubernetes", "intermediate", "cloud"),
        ("AWS", "intermediate", "cloud"),
        ("CI/CD", "intermediate", "devops"),
        ("Git", "expert", "tools"),
        ("Linux", "intermediate", "tools"),
        ("Celery", "intermediate", "tools"),
    ]

    skill_rows: dict[str, ProfileSkill] = {}
    for name, level, category in skill_specs:
        snapshot = {
            "name": name,
            "category": category,
            "level": level,
            "proficiency_note": "",
            "related_technologies": [],
            "notes": "",
            "attachment_path": "",
            "attachment_name": "",
        }
        skill = ProfileSkill(
            user_id=user_id,
            name=name,
            category=category,
            level=level,
            search_key=name.lower(),
            review_state=ReviewState.VERIFIED.value,
            visibility=Visibility.PUBLIC.value,
            approved_snapshot=snapshot,
            approved_version=1,
            version=1,
            reviewed_at=datetime.now(timezone.utc),
        )
        db.add(skill)
        db.flush()
        skill_rows[name] = skill
        counts["profile_skills"] += 1

    project_specs = [
        {
            "title": "Academy Platform — Learning LMS",
            "description": (
                "Architected and shipped a FastAPI + React learning platform serving "
                "10,000+ monthly active learners. Reduced grading latency by 40% through "
                "async task queues with Celery, Redis and PostgreSQL. Cut infrastructure "
                "costs by 35% by containerizing services with Docker and optimizing CI/CD pipelines. "
                "Mentored 3 junior engineers and established code-review standards across the backend team."
            ),
            "repository_url": "https://github.com/hayrullo/academy",
            "live_demo_url": "",
            "start_date": "2024-01",
            "end_date": "",
            "present": True,
            "skill_names": [
                "FastAPI",
                "PostgreSQL",
                "Docker",
                "React",
                "Redis",
                "Celery",
            ],
            "visibility": Visibility.PUBLIC.value,
        },
        {
            "title": "Open Source CLI Codegen Tool",
            "description": (
                "Built a Python CLI that generates typed API clients from OpenAPI 3 specs, "
                "adopted by 500+ developers. Improved developer onboarding time by 50% by "
                "replacing hand-written HTTP clients with generated code. Shipped comprehensive "
                "test coverage (92%) and documentation, earning 350+ GitHub stars."
            ),
            "repository_url": "https://github.com/hayrullo/cli-gen",
            "live_demo_url": "",
            "start_date": "2023-03",
            "end_date": "2023-12",
            "present": False,
            "skill_names": ["Python", "Git", "CI/CD"],
            "visibility": Visibility.PUBLIC.value,
        },
    ]

    for spec in project_specs:
        skill_ids = [
            str(skill_rows[name].id)
            for name in spec["skill_names"]
            if name in skill_rows
        ]
        snapshot = {
            "title": spec["title"],
            "description": spec["description"],
            "skill_ids": skill_ids,
            "start_date": spec["start_date"],
            "end_date": spec["end_date"],
            "present": spec["present"],
            "repository_url": spec["repository_url"],
            "live_demo_url": spec["live_demo_url"],
            "visibility": spec["visibility"],
        }
        project = ProfileProject(
            user_id=user_id,
            title=spec["title"],
            description=spec["description"],
            skill_ids=skill_ids,
            start_date=spec["start_date"],
            end_date=spec["end_date"],
            present=spec["present"],
            repository_url=spec["repository_url"],
            live_demo_url=spec["live_demo_url"],
            review_state=ReviewState.VERIFIED.value,
            visibility=spec["visibility"],
            approved_snapshot=snapshot,
            approved_version=1,
            version=1,
            reviewed_at=datetime.now(timezone.utc),
        )
        db.add(project)
        counts["profile_projects"] += 1

    return counts


def _seed(db: Session, user_id: uuid.UUID) -> dict:
    counts = {
        "skills": 0,
        "languages": 0,
        "projects": 0,
        "experience": 0,
        "certificates": 0,
    }

    skills = [
        # Languages
        ("Python", "expert", "programming_languages"),
        ("TypeScript", "advanced", "programming_languages"),
        ("SQL", "advanced", "programming_languages"),
        # Frameworks
        ("FastAPI", "expert", "frameworks"),
        ("Django", "advanced", "frameworks"),
        ("React", "intermediate", "frameworks"),
        # Databases
        ("PostgreSQL", "advanced", "databases"),
        ("Redis", "intermediate", "databases"),
        ("MongoDB", "intermediate", "databases"),
        # DevOps / Cloud
        ("Docker", "advanced", "devops"),
        ("Kubernetes", "intermediate", "cloud"),
        ("AWS", "intermediate", "cloud"),
        ("CI/CD", "intermediate", "devops"),
        # Tools
        ("Git", "expert", "tools"),
        ("Linux", "intermediate", "tools"),
        ("Celery", "intermediate", "tools"),
    ]
    for i, (name, level, category) in enumerate(skills):
        db.add(
            tm.TalentSkill(
                user_id=user_id,
                name=name,
                level=level,
                category=category,
                status="verified",
                verified=True,
                display_order=i,
            )
        )
    # A private / unapproved skill — must never appear in any CV.
    db.add(
        tm.TalentSkill(
            user_id=user_id,
            name="InternalNdaToolbox",
            level="advanced",
            category="internal",
            status="pending",
            verified=False,
            display_order=len(skills),
        )
    )
    counts["skills"] = len(skills) + 1

    for i, (name, level) in enumerate(
        [
            ("Uzbek", "native"),
            ("English", "fluent"),
            ("Russian", "conversational"),
        ]
    ):
        db.add(
            tm.TalentLanguage(user_id=user_id, name=name, level=level, display_order=i)
        )
    counts["languages"] = 3

    projects = [
        tm.TalentProject(
            user_id=user_id,
            title="Academy Platform — Learning LMS",
            urls=["https://github.com/hayrullo/academy"],
            start_date="2024-01",
            end_date="",
            present=True,
            bullets=[
                "Architected and shipped a FastAPI + React learning platform serving 10,000+ monthly active learners",
                "Reduced grading latency by 40% through async task queues with Celery, Redis and PostgreSQL",
                "Cut infrastructure costs by 35% by containerizing services with Docker and optimizing CI/CD pipelines",
                "Mentored 3 junior engineers and established code-review standards across the backend team",
            ],
            skills=["FastAPI", "PostgreSQL", "Docker", "React", "Redis", "Celery"],
            verified=True,
            hidden=False,
            confidential=False,
            display_order=0,
        ),
        tm.TalentProject(
            user_id=user_id,
            title="Open Source CLI Codegen Tool",
            urls=["https://github.com/hayrullo/cli-gen"],
            start_date="2023-03",
            end_date="2023-12",
            present=False,
            bullets=[
                "Built a Python CLI that generates typed API clients from OpenAPI 3 specs, adopted by 500+ developers",
                "Improved developer onboarding time by 50% by replacing hand-written HTTP clients with generated code",
                "Shipped comprehensive test coverage (92%) and documentation, earning 350+ GitHub stars",
            ],
            skills=["Python", "Click", "Git", "CI/CD"],
            verified=True,
            hidden=False,
            confidential=False,
            display_order=1,
        ),
        # Confidential — must be stripped from CVs / public views.
        tm.TalentProject(
            user_id=user_id,
            title="ACME MegaCorp Secret Client Deal",
            urls=[],
            start_date="2024-06",
            end_date="2024-12",
            present=False,
            bullets=["Internal client work — NDA"],
            skills=["Python"],
            verified=True,
            hidden=False,
            confidential=True,
            display_order=2,
        ),
        # Hidden — owner chose not to show it.
        tm.TalentProject(
            user_id=user_id,
            title="Hidden Internal Side Project",
            urls=[],
            bullets=[],
            skills=[],
            verified=True,
            hidden=True,
            confidential=False,
            display_order=3,
        ),
        # Unverified — must not appear until approved.
        tm.TalentProject(
            user_id=user_id,
            title="Unverified Half-Finished Project",
            urls=[],
            bullets=[],
            skills=[],
            verified=False,
            hidden=False,
            confidential=False,
            display_order=4,
        ),
    ]
    for p in projects:
        db.add(p)
    counts["projects"] = len(projects)

    experiences = [
        tm.TalentExperience(
            user_id=user_id,
            company="Humblebee AI",
            role="Backend Engineer",
            start_date="2023-01",
            end_date="",
            present=True,
            description=(
                "Lead backend architecture for the core AI education platform, owning FastAPI services, "
                "PostgreSQL data layer, and cloud infrastructure.\n"
                "Reduced API p99 latency from 800ms to 180ms by introducing Redis caching and query optimization.\n"
                "Scaled the platform to support 50,000+ daily requests with 99.9% uptime.\n"
                "Built reusable internal tooling that shortened feature delivery time by 25%."
            ),
            technologies=[
                "FastAPI",
                "PostgreSQL",
                "Docker",
                "Redis",
                "Kubernetes",
                "AWS",
            ],
            verified=True,
            hidden=False,
            display_order=0,
        ),
        tm.TalentExperience(
            user_id=user_id,
            company="Startup XYZ",
            role="Software Engineer",
            start_date="2021-06",
            end_date="2022-12",
            present=False,
            description=(
                "Developed customer-facing APIs and internal dashboards for a B2B SaaS product.\n"
                "Shipped 15+ product features end-to-end using Python, Django and React.\n"
                "Improved test coverage from 45% to 85%, reducing production incidents by 30%.\n"
                "Collaborated with product and design teams to refactor the onboarding flow, lifting activation by 20%."
            ),
            technologies=["Python", "Django", "React", "AWS", "PostgreSQL"],
            verified=True,
            hidden=False,
            display_order=1,
        ),
        # Hidden role — must never leak.
        tm.TalentExperience(
            user_id=user_id,
            company="Internal",
            role="Secret Internal Role Title",
            start_date="2020",
            end_date="2021",
            present=False,
            description="Internal",
            technologies=[],
            verified=True,
            hidden=True,
            display_order=2,
        ),
    ]
    for e in experiences:
        db.add(e)
    counts["experience"] = len(experiences)

    certs = [
        tm.TalentCertificate(
            user_id=user_id,
            title="AWS Certified Developer — Associate",
            issuer="Amazon Web Services",
            issue_date="2023-08",
            verified=True,
            hidden=False,
            display_order=0,
        ),
        tm.TalentCertificate(
            user_id=user_id,
            title="Meta Back-End Developer Certificate",
            issuer="Coursera",
            issue_date="2022-05",
            verified=True,
            hidden=False,
            display_order=1,
        ),
        tm.TalentCertificate(
            user_id=user_id,
            title="Docker Certified Associate",
            issuer="Docker, Inc.",
            issue_date="2023-02",
            verified=True,
            hidden=False,
            display_order=2,
        ),
        tm.TalentCertificate(
            user_id=user_id,
            title="PostgreSQL 14 Associate Certification",
            issuer="EnterpriseDB",
            issue_date="2022-11",
            verified=True,
            hidden=False,
            display_order=3,
        ),
        # Hidden cert — must never leak.
        tm.TalentCertificate(
            user_id=user_id,
            title="Secret Internal Certificate",
            issuer="Internal",
            issue_date="2024",
            verified=True,
            hidden=True,
            display_order=4,
        ),
    ]
    for c in certs:
        db.add(c)
    counts["certificates"] = len(certs)

    return counts


def main() -> int:
    p = argparse.ArgumentParser(description="Seed mock talent CV data for a member")
    p.add_argument(
        "--email", default=os.getenv("SEED_EMAIL", "hayrullo.hbai@gmail.com")
    )
    p.add_argument("--password", default=os.getenv("SEED_PASSWORD", "admin12345"))
    args = p.parse_args()

    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True, future=True)
    from app.core.database import Base as _Base

    _Base.metadata.create_all(bind=engine)

    with Session(
        engine, autoflush=False, autocommit=False, expire_on_commit=False
    ) as db:
        user, created = _find_or_create_user(db, args.email, args.password)
        db.flush()
        _clear_talent(db, user.id)
        _clear_profile(db, user.id)
        db.flush()
        counts = _seed(db, user.id)
        counts.update(_seed_profile(db, user.id))
        db.commit()
        email_out = user.email
        uid_out = str(user.id)

    print(f"OK  user_id    = {uid_out}")
    print(f"    email      = {email_out}")
    print(f"    created    = {created}")
    print(f"    login      = {email_out} / {args.password}")
    print(f"    talent     = {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
