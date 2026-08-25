"""Application settings for the Academy Platform (FastAPI port).

Two user sources coexist (see app.identity):
  * local  — users who registered here and go through onboarding.
  * hstaff — users that live in the external hstaff backend
             (https://api-hstaff.humblebee.ai). We authenticate them by
             proxying to hstaff's public /auth/login and mirror a thin copy
             locally, flagged source="hstaff".
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR.parent / ".env")


class Settings:
    APP_NAME = os.getenv("APP_NAME", "Academy Platform")
    ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

    # Sub-path this app is mounted under when behind a path-routing reverse
    # proxy (e.g. "/api" for domain.com/api). Empty = served at the domain root
    # (the subdomain deploy). The proxy strips this prefix; root_path only fixes
    # generated URLs (OpenAPI docs, media absolute URLs).
    ROOT_PATH = os.getenv("ROOT_PATH", "")

    DEV_SECRET_KEY = "dev-secret-key-change-in-production"
    SECRET_KEY = os.getenv("SECRET_KEY", DEV_SECRET_KEY)

    DEBUG = ENVIRONMENT != "production"

    # Comma-separated origin list, or "*" to allow every origin. Because the API
    # is called with credentials, "*" cannot be sent back literally — main.py
    # turns it into an origin regex so the response echoes the caller's origin.
    CORS_ORIGINS = [
        o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()
    ]

    CORS_ALLOW_ALL = "*" in CORS_ORIGINS

    # Database. Normalise a Django/async-style URL to a plain psycopg2 one.
    DATABASE_URL = (
        os.getenv(
            "DATABASE_URL", "postgresql://academy:academy@localhost:5432/academy_db"
        )
        .replace("+asyncpg", "")
        .replace("postgres://", "postgresql://")
    )

    # Uploaded files (payment / address proofs). Served under /media in DEBUG.
    MEDIA_ROOT = BASE_DIR / "media"
    MEDIA_URL = "/media/"
    STATIC_ROOT = BASE_DIR / "staticfiles"

    TIME_ZONE = "Asia/Tashkent"

    # ---- Email (forgot-password) ----
    DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "no-reply@academy.local")
    FRONTEND_RESET_URL = os.getenv(
        "FRONTEND_RESET_URL", "http://localhost:3000/reset-password"
    )

    # Public base URL of the frontend, used for the CV footer QR code that
    # links to the user's public profile (e.g. https://iclass.ai/profile/john).
    FRONTEND_PUBLIC_URL = os.getenv("FRONTEND_PUBLIC_URL", "https://iclass.ai")

    # ---- Seed admin ----
    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@academy.local")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin12345")

    # ---- academy: Judge0 (problems / exam code questions) ----
    JUDGE0_URL = os.getenv("JUDGE0_URL", "http://host.docker.internal:2358")
    JUDGE0_AUTH_TOKEN = os.getenv("JUDGE0_AUTH_TOKEN", "")
    JUDGE0_AUTH_HEADER = os.getenv("JUDGE0_AUTH_HEADER", "X-Auth-Token")

    # ---- academy: ml-runner (Data Lab) ----
    MLRUNNER_URL = os.getenv("MLRUNNER_URL", "http://ml-runner:8080/run")
    MLRUNNER_TIMEOUT = int(os.getenv("MLRUNNER_TIMEOUT", "140"))

    # ---- academy: cv-generator (CV rendering) ----
    # CV_SERVICE_URL is the legacy name used in early deploys; CV_GENERATOR_URL
    # is the canonical name going forward. Either works.
    CV_GENERATOR_URL = os.getenv(
        "CV_GENERATOR_URL", os.getenv("CV_SERVICE_URL", "http://cv-generator:8080")
    )
    CV_GENERATOR_TIMEOUT = int(os.getenv("CV_GENERATOR_TIMEOUT", "30"))

    # ---- hstaff integration ----
    HSTAFF_BASE_URL = os.getenv(
        "HSTAFF_BASE_URL", "https://api-hstaff.humblebee.ai"
    ).rstrip("/")
    HSTAFF_API_PREFIX = os.getenv("HSTAFF_API_PREFIX", "/api/v1")
    HSTAFF_ENABLED = os.getenv("HSTAFF_ENABLED", "true").lower() == "true"
    HSTAFF_TIMEOUT = float(os.getenv("HSTAFF_TIMEOUT", "10"))
    # Privileged service account (HR credentials) used to register users on
    # hstaff on behalf of our admins/academy-managers. Targets the same
    # HSTAFF_BASE_URL / HSTAFF_API_PREFIX as the rest of the integration.
    HSTAFF_SERVICE_EMAIL = os.getenv("HSTAFF_SERVICE_EMAIL", "")
    HSTAFF_SERVICE_PASSWORD = os.getenv("HSTAFF_SERVICE_PASSWORD", "")
    # hstaff route that returns a student's assigned mentor, used to route
    # profile verification requests to the right reviewer (see
    # app/profile/mentorship.py). Supports the placeholders {username},
    # {email} and {user_id}; empty disables mentor routing, which leaves
    # submissions in the shared queue instead.
    HSTAFF_MENTOR_PATH = os.getenv("HSTAFF_MENTOR_PATH", "")
    # hstaff route that returns a user's assigned mentees (reverse of the
    # mentor path), used to auto-grant the local "mentor" role at login to
    # anyone hstaff says mentors someone, and to mirror the mentees' emails
    # locally (see app/identity/services.py `sync_mentor_role`). Supports the
    # same placeholders ({username}, {email}, {user_id}) as HSTAFF_MENTOR_PATH;
    # empty disables the check, which leaves role assignment purely manual.
    HSTAFF_MENTEES_PATH = os.getenv("HSTAFF_MENTEES_PATH", "")
    # Admin-scoped hstaff route listing *every* mentorship assignment, used by
    # scripts/backfill_mentorships.py to seed the local mirror in one pass.
    # Unlike the two paths above this one is global, not self-scoped, so it goes
    # out under the service account (HSTAFF_SERVICE_*) rather than a user token.
    HSTAFF_ASSIGNMENTS_PATH = os.getenv(
        "HSTAFF_ASSIGNMENTS_PATH", "/mentorship/assignments"
    )

    def __init__(self):
        if self.ENVIRONMENT == "production" and self.SECRET_KEY == self.DEV_SECRET_KEY:
            raise RuntimeError("Set a real SECRET_KEY before running in production.")
        self.MEDIA_ROOT.mkdir(parents=True, exist_ok=True)


settings = Settings()
