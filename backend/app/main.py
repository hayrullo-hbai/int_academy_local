"""Academy Platform backend — FastAPI application.

Mirrors the Django/DRF backend: dual-source auth (local + hstaff proxy), RBAC,
the onboarding pipeline (with Google-Sheet sync + chats), and hstaff passthrough.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.academy.router import router as academy_router
from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from sqlalchemy import text as sa_text
from app.hstaff.router import router as hstaff_router
from app.identity.router import router as identity_router
from app.notifications.router import router as notifications_router
from app.onboarding.router import router as onboarding_router
from app.talent.router import router as talent_router
from app.profile.router import router as profile_router

# Import model modules so every table is registered on Base.metadata.
from app.academy import models as _academy_models  # noqa: F401
from app.identity import models as _identity_models  # noqa: F401
from app.onboarding import models as _onboarding_models  # noqa: F401
from app.talent import models as _talent_models  # noqa: F401
from app.profile import models as _profile_models  # noqa: F401

logging.basicConfig(level=logging.INFO)

app = FastAPI(title=settings.APP_NAME, root_path=settings.ROOT_PATH)

app.add_middleware(
    CORSMiddleware,
    # With allow_credentials the spec forbids a literal "*" in the response
    # header, so allow-all is expressed as a catch-all regex instead: the
    # middleware then echoes back whatever Origin the caller sent.
    allow_origins=[] if settings.CORS_ALLOW_ALL else settings.CORS_ORIGINS,
    allow_origin_regex=".*" if settings.CORS_ALLOW_ALL else None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _humanize_location(loc: tuple) -> str:
    """Turn a pydantic error location like ("body", "test_cases", 2,
    "expected_output") into readable text: 'Test case #3 → expected output'."""
    parts = [p for p in loc if p != "body"]
    labels = []
    i = 0
    while i < len(parts):
        part = parts[i]
        # "<field>", <index> → "Field #<index+1>"
        if i + 1 < len(parts) and isinstance(parts[i + 1], int):
            name = str(part).replace("_", " ").rstrip("s").capitalize()
            labels.append(f"{name} #{parts[i + 1] + 1}")
            i += 2
        else:
            labels.append(str(part).replace("_", " "))
            i += 1
    return " → ".join(labels)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """Collapse FastAPI's verbose validation dump into one readable message,
    keeping the `{"detail": ...}` shape the frontend already reads."""
    messages = []
    for err in exc.errors():
        # Our own validators raise "Value error, <message>"; strip the prefix.
        msg = err.get("msg", "Invalid value")
        msg = msg.replace("Value error, ", "").strip()
        where = _humanize_location(err.get("loc", ()))
        if err.get("type") == "missing":
            messages.append(f"{where or 'A required field'} is required.")
        elif where and where.lower() not in msg.lower():
            messages.append(f"{where}: {msg}")
        else:
            messages.append(msg)
    # De-duplicate while preserving order.
    seen = set()
    unique = [m for m in messages if not (m in seen or seen.add(m))]
    return JSONResponse({"detail": " ".join(unique)}, status_code=422)


@app.on_event("startup")
def _startup():
    # Create the schema, then seed RBAC + the superadmin (idempotent).
    Base.metadata.create_all(bind=engine)

    # Idempotent column migrations (create_all never adds columns to existing tables).
    from sqlalchemy import inspect

    def _drop_column_if_present(table: str, name: str) -> None:
        """Retire a column the models no longer carry. Without this an existing
        database keeps the old NOT NULL column and every INSERT fails."""
        if name in [c["name"] for c in inspect(engine).get_columns(table)]:
            with engine.connect() as conn:
                conn.execute(sa_text(f"ALTER TABLE {table} DROP COLUMN {name}"))
                conn.commit()

    def _drop_table_if_present(table: str) -> None:
        """Retire a whole table the models no longer define."""
        if inspect(engine).has_table(table):
            with engine.connect() as conn:
                conn.execute(sa_text(f"DROP TABLE IF EXISTS {table}"))
                conn.commit()

    def _add_column_if_missing(table: str, name: str, ddl: str) -> None:
        if name not in [c["name"] for c in inspect(engine).get_columns(table)]:
            with engine.connect() as conn:
                conn.execute(sa_text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))
                conn.commit()

    _add_column_if_missing(
        "roles",
        "created_by_id",
        "created_by_id UUID REFERENCES users(id) ON DELETE SET NULL",
    )
    _add_column_if_missing(
        "users",
        "academy_progress_public",
        "academy_progress_public BOOLEAN NOT NULL DEFAULT FALSE",
    )
    _add_column_if_missing(
        "academy_submissions",
        "user_id",
        "user_id UUID REFERENCES users(id) ON DELETE SET NULL",
    )
    _drop_column_if_present(
        "talent_experience",
        "company_logo_url",
    )
    _add_column_if_missing(
        "academy_data_submissions",
        "user_id",
        "user_id UUID REFERENCES users(id) ON DELETE SET NULL",
    )
    _add_column_if_missing(
        "academy_data_submissions",
        "cell_results",
        "cell_results JSONB NOT NULL DEFAULT '[]'::jsonb",
    )
    _add_column_if_missing(
        "academy_data_submissions",
        "text_answers",
        "text_answers JSONB NOT NULL DEFAULT '{}'::jsonb",
    )
    _add_column_if_missing(
        "academy_data_submissions",
        "score",
        "score INTEGER NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        "academy_data_submissions",
        "max_score",
        "max_score INTEGER NOT NULL DEFAULT 0",
    )
    _add_column_if_missing(
        "academy_notebooks",
        "is_public",
        "is_public BOOLEAN NOT NULL DEFAULT FALSE",
    )
    _add_column_if_missing(
        "academy_problems",
        "owner_id",
        "owner_id UUID REFERENCES users(id) ON DELETE SET NULL",
    )
    _add_column_if_missing(
        "academy_datasets",
        "uploaded_by_id",
        "uploaded_by_id UUID REFERENCES users(id) ON DELETE SET NULL",
    )
    _add_column_if_missing(
        "academy_data_problems",
        "owner_id",
        "owner_id UUID REFERENCES users(id) ON DELETE SET NULL",
    )
    _add_column_if_missing(
        "academy_exams",
        "owner_id",
        "owner_id UUID REFERENCES users(id) ON DELETE SET NULL",
    )
    _add_column_if_missing(
        "academy_exam_attempts",
        "submitted",
        "submitted BOOLEAN NOT NULL DEFAULT FALSE",
    )
    _add_column_if_missing(
        "academy_questions",
        "data_problem_cells",
        "data_problem_cells JSONB NOT NULL DEFAULT '[]'::jsonb",
    )
    _add_column_if_missing(
        "academy_questions",
        "data_problem_checker_code",
        "data_problem_checker_code TEXT NOT NULL DEFAULT ''",
    )
    _add_column_if_missing(
        "academy_exam_problems",
        "problem_slug",
        "problem_slug VARCHAR(100)",
    )
    _add_column_if_missing(
        "academy_questions",
        "data_problem_slug",
        "data_problem_slug VARCHAR(100)",
    )
    _add_column_if_missing(
        "academy_exams",
        "access_type",
        "access_type VARCHAR(10) NOT NULL DEFAULT 'public'",
    )
    _add_column_if_missing(
        "profile_verifications",
        "assigned_mentor_id",
        "assigned_mentor_id UUID REFERENCES users(id) ON DELETE SET NULL",
    )
    _add_column_if_missing(
        "profile_verifications",
        "academy_manager_review",
        "academy_manager_review BOOLEAN NOT NULL DEFAULT FALSE",
    )
    _add_column_if_missing(
        "profile_shares",
        "sections",
        "sections JSONB NOT NULL DEFAULT '{}'::jsonb",
    )
    _add_column_if_missing(
        "onboarding_pipelines",
        "final_decision",
        "final_decision VARCHAR(20) NOT NULL DEFAULT ''",
    )
    for _talent_table in (
        "talent_skills",
        "talent_languages",
        "talent_projects",
        "talent_experience",
        "talent_certificates",
    ):
        _add_column_if_missing(
            _talent_table,
            "source_id",
            "source_id VARCHAR(64) NOT NULL DEFAULT ''",
        )

    _add_column_if_missing(
        "profile_projects",
        "skill_ids",
        "skill_ids JSONB NOT NULL DEFAULT '[]'::jsonb",
    )
    _add_column_if_missing(
        "profile_skills",
        "attachment_path",
        "attachment_path VARCHAR(300) NOT NULL DEFAULT ''",
    )
    _add_column_if_missing(
        "profile_skills",
        "attachment_name",
        "attachment_name VARCHAR(200) NOT NULL DEFAULT ''",
    )

    # Retired profile fields: the reviewer-assigned verification level, and the
    # project's tags / role / team members / kind / confidentiality / public summary.
    for _table in (
        "profile_skills",
        "profile_projects",
        "profile_social_accounts",
        "profile_verifications",
    ):
        _drop_column_if_present(_table, "verification_level")
    for _column in (
        "public_summary",
        "public_summary_approved",
        "tags",
        "role",
        "team_members",
        "kind",
        "confidentiality",
        "screenshots",
        "attachments",
        "technologies",
    ):
        _drop_column_if_present("profile_projects", _column)

    # Retired audit trail — visibility/publication history is no longer kept.
    _drop_table_if_present("profile_visibility_audits")

    from app.seed import seed_admin

    db = SessionLocal()
    try:
        seed_admin(db)
    finally:
        db.close()


@app.get("/health")
def health_check():
    return {"status": "ok", "service": settings.APP_NAME}


# Uploaded proof files (Django served these under /media in DEBUG).
app.mount("/media", StaticFiles(directory=str(settings.MEDIA_ROOT)), name="media")

app.include_router(onboarding_router)
app.include_router(hstaff_router)
app.include_router(identity_router)
app.include_router(academy_router)
# After identity: identity owns GET /profile/{email}; this router adds the
# deeper /profile/{ident}/skills|projects|social-accounts paths beneath it.
app.include_router(profile_router)
# Owner-facing feed of the staff overrides the profile services write.
app.include_router(notifications_router)
app.include_router(talent_router)
