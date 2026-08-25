# Backend — Deployment & Migration Guide

FastAPI + PostgreSQL service for the Academy platform (auth/RBAC, onboarding,
academy problems/exams, submissions). This repo is **self-contained**: it ships
its own `Dockerfile` and `docker-compose.yml` and can run on its own, or as part
of the full stack (see the root `DEPLOYMENT.md`).

- **Repo:** `https://github.com/humblebeeai-academy/academy-platform` (backend/ directory)
- **Serves:** HTTP API on container port `8000` (published as `8001` locally)
- **API docs:** `http://<host>:8001/docs`

---

## 1. Runtime dependencies

| Dependency        | Required? | Notes                                                         |
|-------------------|-----------|---------------------------------------------------------------|
| PostgreSQL 16     | **Yes**   | Provided by the `db` service in this repo's compose.          |
| Judge0            | Optional  | Code execution for academy coding problems. See root stack.   |
| ml-runner         | Optional  | Runs ML/data-problem cells. See root stack.                   |
| hstaff API        | Optional  | External integration; disable with `HSTAFF_ENABLED=false`.    |

The app's **startup hook creates the schema (`create_all`) and seeds RBAC + the
admin user** — all idempotent. There is no separate migration step to run.

---

## 2. Moving to a new server (git)

On the **old** machine, make sure everything is pushed:

```bash
cd backend
git add -A && git commit -m "chore: pre-migration snapshot"   # if you have changes
git push origin firdavs        # current working branch
```

On the **new** server:

```bash
git clone https://github.com/humblebeeai-academy/int-academy-platform-backend
cd backend
git checkout firdavs
```

> ⚠️ **`.env` is git-ignored and does NOT travel with the repo.** You must copy
> it (or recreate it) on the new server manually — see §3. Likewise the
> `secrets/` directory (Google service-account JSON) is not committed.

**Carry these out-of-git files separately** (scp / rsync / vault):

```bash
# from old server → new server
scp .env            newhost:/path/backend/.env
scp -r secrets      newhost:/path/backend/secrets   # if used
```

---

## 3. Environment variables

Create `.env` in the repo root. Keys the backend reads:

```dotenv
# Core
APP_NAME=Academy Platform
ENVIRONMENT=production
SECRET_KEY=<long-random-string>          # CHANGE for a new deployment
CORS_ORIGINS=https://your-frontend-host  # comma-separated

# Database (the compose db service; host is the service name `db`)
DATABASE_URL=postgresql://academy:academy@db:5432/academy_db
POSTGRES_USER=academy
POSTGRES_PASSWORD=<change-me>
POSTGRES_DB=academy_db

# Seed admin (created on first boot)
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=<change-me>

# hstaff integration (set ENABLED=false to skip)
HSTAFF_BASE_URL=
HSTAFF_API_PREFIX=
HSTAFF_ENABLED=false

# Judge0 (self-hosted) — omit if not running coding problems
JUDGE0_URL=http://judge0-server:2358
JUDGE0_AUTH_TOKEN=<token>
JUDGE0_AUTH_HEADER=X-Auth-Token

# ml-runner (self-hosted) — omit if not running data problems
MLRUNNER_URL=http://ml-runner:8080
MLRUNNER_TIMEOUT=120

# Optional Google Sheet onboarding sync
GOOGLE_SERVICE_ACCOUNT_FILE=/app/secrets/gsheet-service-account.json
ONBOARDING_SHEET_ID=
ONBOARDING_SHEET_GID=
```

> **Service names vs. localhost:** inside the Docker network, use service names
> (`db`, `judge0-server`, `ml-runner`). Only use `localhost`/host ports when a
> service runs outside Docker.

---

## 4. Run it (standalone)

This repo's `docker-compose.yml` brings up **backend + Postgres**:

```bash
docker compose up -d --build
docker compose logs -f backend          # watch startup, schema create, seed
curl http://localhost:8001/docs         # health check
```

Ports published locally:

| Service  | Container | Host   |
|----------|-----------|--------|
| backend  | 8000      | `8001` |
| db       | 5432      | `5433` |

### Persistent data

Two named Docker volumes hold state — **back these up before a move**:

- `postgres_data` — the database
- `media_data` — uploaded files (datasets, onboarding/address proofs)

```bash
# Dump the DB (recommended over copying the volume):
docker compose exec db pg_dump -U academy academy_db > academy_db.sql
# Restore on the new server (after `docker compose up -d db`):
cat academy_db.sql | docker compose exec -T db psql -U academy academy_db

# Media files:
docker run --rm -v int-academy-platform_media_data:/m -v "$PWD":/b alpine \
  tar czf /b/media.tgz -C /m .
```

---

## 5. Post-migration checklist

- [ ] `.env` present with a fresh `SECRET_KEY` and real DB/admin passwords
- [ ] `secrets/` copied if Google Sheets sync is used
- [ ] `postgres_data` restored (or fresh DB accepted — admin re-seeds from `.env`)
- [ ] `media_data` restored if you need existing uploads
- [ ] `CORS_ORIGINS` points at the new frontend URL
- [ ] Judge0 / ml-runner reachable (or their env vars removed)
- [ ] `curl http://<host>:8001/docs` returns 200
- [ ] Log in with `ADMIN_EMAIL` / `ADMIN_PASSWORD`
