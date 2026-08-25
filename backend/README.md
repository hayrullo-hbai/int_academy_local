# Academy Platform — Backend

## Purpose

This repository is the FastAPI + PostgreSQL backend behind the HumbleBee Academy.
It owns identity and access control, the candidate onboarding pipeline, the coding
and data-science academy, and integration with the external hstaff HR system. It
exists to give the Academy's web frontends a single API and a single identity to
reason about across two user populations (local accounts and mirrored hstaff
accounts).

## ✨ Features

- **Identity & RBAC** — dual-source authentication (local accounts plus mirrored
  hstaff accounts), roles, permissions, and user administration. Issues the
  platform's own JWT access and refresh tokens regardless of login source.
- **Onboarding** — the six-stage candidate pipeline fed by a Google Sheet, with
  per-stage interviewer assignment, reports, chats, and payment approval.
- **Academy** — coding problems, datasets, notebooks, data problems, and exams,
  auto-graded by Judge0 and a self-hosted ml-runner.
- **hstaff integration** — typed calls plus a generic authenticated passthrough
  proxy to the external HR system at `api-hstaff.humblebee.ai`.
- **Health checks** — a `/health` liveness endpoint and interactive API docs at
  `/docs` for local and deployment verification.

## Ownership

- Team: software-engineering
- Maintainers: `@humblebeeai-academy/software-engineering`

## Prerequisites

- [Python](https://www.python.org/downloads/) 3.12+ — application runtime.
- [PostgreSQL](https://www.postgresql.org/download/) 16+ — primary database; the
  app does not start without a reachable instance.
- [Docker](https://docs.docker.com/get-docker/) & [Docker Compose](https://docs.docker.com/compose/) — recommended local runtime (brings up the service and its database).
- [Judge0](https://github.com/judge0/judge0) — code-execution engine for coding
  problems and exam code questions. Optional locally; only the grading endpoints
  fail without it.
- ml-runner — self-hosted execution service for Data Lab, notebooks, and data
  problems (internal service, no public install page). Optional locally; only the
  relevant grading endpoints fail without it.
- hstaff API — external HR system used for hstaff-sourced login, profiles, and
  analytics. Optional locally: set `HSTAFF_ENABLED=false` to run fully standalone.

### Credentials

Required only for the features that use them; the app boots without them in a
standalone local setup.

- `SECRET_KEY` — signs JWTs. Any value works in development, but the app **refuses
  to boot in production** with the built-in dev value. Generate a strong random
  secret for any deployed environment.
- `ADMIN_EMAIL` / `ADMIN_PASSWORD` — seed the startup superadmin. Change these
  before any deployment.
- `GOOGLE_SERVICE_ACCOUNT_FILE` — Google service-account JSON used to read the
  onboarding sheet. Scope: read access to the onboarding spreadsheet. Falls back
  to public CSV export when absent. Create via the Google Cloud console.
- `HSTAFF_SERVICE_EMAIL` / `HSTAFF_SERVICE_PASSWORD` — privileged hstaff HR account
  used to register and bulk-sync hstaff users. Scope: hstaff user administration.
  Request from the hstaff/HR system owners.
- `JUDGE0_AUTH_TOKEN` — optional auth token for a secured Judge0 instance.

## Quick Start

```bash
git clone https://github.com/humblebeeai-academy/int-academy-platform-backend.git
cd int-academy-platform-backend

# Recommended: backend + PostgreSQL via Docker
docker compose up --build
```

The backend is served on `http://localhost:8000`, with interactive API docs at
`http://localhost:8000/docs`. Verify it is running:

```bash
curl http://localhost:8000/health
```

On first startup the app creates the schema, applies its idempotent column
migrations, and seeds the RBAC catalog plus a superadmin from `ADMIN_EMAIL` /
`ADMIN_PASSWORD`. **Change those defaults before any deployment.**

## Configuration

All settings are read from the environment and loaded automatically from `.env`.
[`.env.example`](.env.example) is the source of truth — copy it and fill in local
values:

```bash
cp .env.example .env
```

The variables that most often need context:

| Variable | Default | Notes |
|---|---|---|
| `ENVIRONMENT` | `development` | `production` enables strict checks. |
| `SECRET_KEY` | dev key | Signs JWTs. App refuses to boot in production with the dev value. Use `<STRONG_RANDOM_SECRET>`. |
| `DATABASE_URL` | local postgres | `+asyncpg` and `postgres://` forms are normalised automatically. |
| `CORS_ORIGINS` | `*` | `*` is turned into an origin regex, since credentialed CORS forbids a literal `*`. |
| `ROOT_PATH` | `""` | Set to e.g. `/api` when mounted under a path-routing proxy. |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | `admin@academy.local` / `<ADMIN_PASSWORD>` | Seeded superadmin. Change before deploying. |
| `JUDGE0_URL` | `http://host.docker.internal:2358` | Coding-problem execution. |
| `MLRUNNER_URL` | `http://ml-runner:8080/run` | Data Lab / data-problem execution. |
| `HSTAFF_ENABLED` | `true` | Set `false` to run fully standalone. |
| `HSTAFF_BASE_URL` | `https://api-hstaff.humblebee.ai` | External HR API. |
| `ONBOARDING_SHEET_ID` / `ONBOARDING_SHEET_GID` | team sheet | Default onboarding sheet. |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | `/app/secrets/<FILE>.json` | Preferred sheet access; falls back to public CSV export. |

## Security

This repository uses JWT signing secrets, a seeded admin credential, a Google
service account, an hstaff service account, and optional Judge0 auth. Never commit
real secrets, tokens, private keys, or production `.env` files.

### Credential handling

- **Local development**
  - Use [`.env.example`](.env.example) as the template for safe placeholder values.
  - Use sandbox or development credentials only; keep the seeded admin defaults out
    of any shared environment.
  - Run with `HSTAFF_ENABLED=false` when you do not need the HR integration, so no
    hstaff credentials are required on your machine.
- **Deployed environments**
  - Set a strong, unique `SECRET_KEY`; the app enforces this by refusing to boot in
    production with the dev value.
  - Change `ADMIN_EMAIL` / `ADMIN_PASSWORD` from their defaults.
  - Store the Google and hstaff service-account credentials in your environment's
    secret storage, not in the repository.

### Operational note

**Everything under `MEDIA_ROOT` is served unauthenticated at `/media`** with
unguessable UUID filenames — dataset CSVs, payment and address proofs, profile
certificates, and skill evidence attachments. This is obscurity, not access
control: treat those URLs as secrets and do not share them.

Note in particular that profile items carry per-item visibility rules and a
review workflow, and the download endpoints (`/profile/certificates/{id}/download`)
enforce them — but the underlying `/media/...` path does not. Anyone holding the
raw URL can fetch the file regardless of who the item is shared with. Serving
these through an authorised handler instead of the static mount is the fix; until
then the guarantee is only as strong as the UUID.

### If a secret is exposed

- Revoke or rotate the credential immediately.
- Remove it from local files and, if needed, from repository history.
- Notify the software-engineering team.

## Detailed Local Setup

Use this when you want to run the app directly rather than via Docker.

Start a local PostgreSQL (the simplest option is the Compose database):

```bash
docker compose up -d db          # exposes 5433 on the host
```

Create and activate a virtual environment, then install dependencies:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Create local configuration and point it at your database:

```bash
cp .env.example .env
# in .env:
# DATABASE_URL=postgresql://academy:academy@localhost:5433/academy_db
```

Run the API with autoreload:

```bash
uvicorn app.main:app --reload --port 8000
```

In Docker deployments, [`entrypoint.sh`](entrypoint.sh) additionally waits for the
database, creates media directories, and drops privileges to `appuser` before
starting the server.

Judge0 and ml-runner are optional; only submission/grading endpoints need them.
Set `HSTAFF_ENABLED=false` to work fully offline — local login still works with the
seeded admin. See [docs/contributing.md](docs/contributing.md) for the full
contributor workflow and the traps to avoid.

## Common Commands

```bash
python -m venv .venv && source .venv/bin/activate   # create/activate virtualenv
pip install -r requirements.txt                     # install dependencies
cp .env.example .env                                # create local configuration
uvicorn app.main:app --reload --port 8000           # run the API locally
docker compose up --build                           # run backend + PostgreSQL
docker compose up -d db                             # run only the database
```

## Testing and Quality

This repository has **no automated test suite and no linter configured**;
verification is manual. Before opening a PR, exercise changed endpoints through the
interactive docs at `http://localhost:8000/docs` or the frontend, and confirm the
service still starts and passes its health check:

```bash
curl http://localhost:8000/health
```

The [security checklist in docs/contributing.md](docs/contributing.md) is the
required manual review step for any change touching routes or serialization.

## Documentation

| Document | What it covers |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Package layout, request lifecycle, layering rules, data model |
| [docs/system-design.md](docs/system-design.md) | Runtime topology, auth design, grading pipelines, schema migration strategy, failure modes |
| [docs/features.md](docs/features.md) | Every feature: purpose, endpoints, and who may do what |
| [docs/roles.md](docs/roles.md) | The role catalog and the full permission matrix |
| [docs/contributing.md](docs/contributing.md) | How to set up, make changes safely, and the traps to avoid |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Deploying and moving the service between servers |

### Related references

- [FastAPI documentation](https://fastapi.tiangolo.com/) — web framework.
- [SQLAlchemy 2.0 documentation](https://docs.sqlalchemy.org/en/20/) — ORM and database layer.
- [PostgreSQL documentation](https://www.postgresql.org/docs/) — database behavior and setup.
- [Judge0 documentation](https://ce.judge0.com/) — code-execution engine used for grading.
- [Docker Compose documentation](https://docs.docker.com/compose/) — local multi-service setup.

## Layout

```
app/
├── main.py              FastAPI app: CORS, error shaping, startup schema+seed
├── seed.py              Idempotent RBAC catalog + superadmin seeding
├── core/                Cross-cutting: config, db session, security, deps, files
├── identity/            Users, roles, permissions, auth, hstaff mirroring
├── onboarding/          Candidate pipeline, stages, chats, Google Sheet sync
├── academy/             Problems, datasets, notebooks, data problems, exams
├── hstaff/              Client + router for the external HR API
└── talent/              Talent CV sync from hstaff, generation/storage/render
```

Each domain package follows the same shape: `models.py` (SQLAlchemy) →
`services.py` (business logic) → `router.py` (HTTP), with `access.py` /
`enums.py` for authorization rules and constants. See
[docs/architecture.md](docs/architecture.md).

## Things that will surprise you

- **There is no Alembic.** The startup hook runs `Base.metadata.create_all`
  followed by a hand-written list of `_add_column_if_missing` calls in
  [`app/main.py`](app/main.py). Adding a column to an existing table means
  adding a line there, or it will silently not exist in any deployed database.
- **Authorization is inline, not injected.** `core/deps.py` only authenticates.
  Every route calls a small role helper itself. There are no permission
  dependency factories, and there is no default-deny — a route without a check is
  open to every authenticated user.
- **Role rules are duplicated in the frontend** at `frontend/lib/roles.ts`.
  Change one, change the other.
- **Login has two sources**, routed by the user's `source` column — not by
  retry. A known local user is authenticated locally and *never* falls through
  to hstaff. An unknown email or a known hstaff user is proxied to hstaff, which
  mirrors them locally on success. Either way *we* issue the token.
- **A local row shadows hstaff login.** If a `users` row with `source='local'`
  exists for an email that is also an hstaff account, hstaff is never consulted
  and the local password is demanded instead — the UI just shows
  "Invalid email or password". To restore hstaff login, flip the row to hstaff
  (`UPDATE users SET source='hstaff', password_hash=NULL WHERE email=...`),
  or delete it and let the next login re-mirror it.
- **hstaff login failures are logged, not surfaced.** `login_user` returns the
  same generic 401 for both rejected credentials and an unreachable hstaff; the
  distinction is visible only in the backend log (`WARNING` on hstaff auth
  rejection, `ERROR` on network / 5xx from `app/hstaff/client.py`).
- **Errors are returned, not raised.** Most routes `return _err(...)` (a
  `JSONResponse`) rather than raising `HTTPException`, so the declared
  `response_model` is bypassed on the error path.
