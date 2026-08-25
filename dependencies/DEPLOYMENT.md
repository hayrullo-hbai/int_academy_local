# Platform Dependencies — Judge0 & ML-Runner

Self-hosted execution backends for the Academy platform:

- **`judge0/`** — [Judge0](https://github.com/judge0/judge0) code-execution engine
  (vendored). Runs learners' coding-problem submissions in sandboxes.
- **`ml-runner/`** — a small Flask/Gunicorn service that executes ML / data-problem
  code cells against uploaded datasets.

Both are consumed by `int-academy-platform-backend`. They are **not started by
this repo's own compose** — they run as part of the **root** `docker-compose.yml`
(one folder up), which references them by relative path:

```
academy/
├── docker-compose.yml                 # ← orchestrates everything
├── int-academy-platform-backend/
├── int-academy-platfrom-frontend/
└── int-academy-platform-dependencies/ # ← THIS repo
    ├── judge0/
    └── ml-runner/
```

> ⚠️ **Path contract with the root compose.** The root compose expects this repo
> to be cloned as `int-academy-platform-dependencies/` next to the other repos.
> It reads:
> - `./int-academy-platform-dependencies/judge0/judge0.conf`
> - `build: ./int-academy-platform-dependencies/ml-runner`
>
> If you rename or move this folder, update those paths in the root
> `docker-compose.yml` or the stack will fail with *"env file … judge0.conf not
> found"*.

---

## 1. Moving to a new server (git)

`judge0/` and `ml-runner/` are **committed directly** in this repo (vendored —
no submodules), so a plain clone brings everything:

```bash
cd ~/academy   # the parent folder that holds all the repos
git clone https://github.com/humblebeeai-academy/int-academy-platform-dependencies
# branch: main — judge0/ and ml-runner/ are already fully populated
```

> **`judge0/judge0.conf` is the ONE file that does NOT travel in git.** It holds
> the Judge0 auth tokens + Redis/Postgres passwords, so it's git-ignored (see
> `.gitignore`). Move it separately — `scp` it over like the root `.env`:
> ```bash
> scp judge0/judge0.conf newhost:~/academy/int-academy-platform-dependencies/judge0/judge0.conf
> ```
> `JUDGE0_AUTH_TOKEN` in the root `.env` must match `AUTHN/AUTHZ_TOKEN` in that file.
> A `judge0/judge0.conf.example` (tokens blanked) is a good idea to commit as a
> template.

### Updating judge0 later

judge0 is a vendored copy of upstream `judge0/judge0`. To refresh it, pull the
files from upstream into `judge0/` and commit the diff like any other change.

---

## 2. Judge0

**Image:** `judge0/judge0:latest` (no build). Four containers in the root compose:
`judge0-server`, `judge0-worker`, `judge0-db` (Postgres 16.2), `judge0-redis`.

- **Port:** `2358` (host) → the backend reads it via `JUDGE0_URL` in the root `.env`.
- **Config:** all tuning + secrets live in `judge0/judge0.conf`, mounted read-only
  into server/worker and used as the `env_file` for db/redis. Keys of note:
  - `AUTHN_TOKEN` / `AUTHZ_TOKEN` — must match the backend's `JUDGE0_AUTH_TOKEN`.
  - `REDIS_HOST`, and `REDIS_PASSWORD` (via the root `.env`).
- **Requires `privileged: true`** and kernel cgroup access. Some cloud VMs block
  this — verify a submission actually runs after any move.
- **Persistent data:** the `judge0_data` Docker volume (its Postgres).

### Backend wiring (root `.env`)

```dotenv
JUDGE0_URL=http://judge0-server:2358   # service name on the Docker network
JUDGE0_AUTH_TOKEN=<must equal AUTHN_TOKEN/AUTHZ_TOKEN in judge0.conf>
JUDGE0_AUTH_HEADER=X-Auth-Token
```

### Health check

```bash
curl http://localhost:2358/about                       # from the host
docker compose exec backend curl -s http://judge0-server:2358/languages | head   # from the network
```

---

## 3. ML-Runner

**Built from `ml-runner/`** (`python:3.12-slim` + Gunicorn). Single container.

- **Port:** `8080` (host) → backend reads it via `MLRUNNER_URL`.
- **Entrypoint:** `gunicorn -b 0.0.0.0:8080 -w 2 -t 140 server:app`
  (the worker timeout **must exceed** `ML_TIMEOUT`).
- **Environment** (set in the root compose / `.env`):

  | Var          | Default | Meaning                                    |
  |--------------|---------|--------------------------------------------|
  | `ML_TIMEOUT` | `120`   | Max seconds per code run.                  |
  | `ML_MEM_MB`  | `1024`  | Memory cap per run.                        |
  | `ML_FSIZE_MB`| `50`    | Max output file size.                      |

- **Volumes:** `ml_datasets` volume mounted at `/datasets` (uploaded datasets the
  runner reads). Back it up before a move if you have live data.

### Backend wiring (root `.env`)

```dotenv
MLRUNNER_URL=http://ml-runner:8080     # service name on the Docker network
MLRUNNER_TIMEOUT=120
```

### Health check

```bash
docker compose exec backend curl -s http://ml-runner:8080/health   # adjust path to server.py
```

---

## 4. ngrok — no changes needed

ngrok tunnels reference services by **Docker service name**, not folder path, so
moving these into this repo does **not** affect tunneling. The root `ngrok.yml`
still tunnels `ml-runner:8080` (and `frontend:3000`, `backend:8000`) correctly.

Only two ngrok facts to remember after any move:
- `ngrok.yml` and the `ngrok` service live in the **root** repo, not here.
- Free-plan ngrok URLs change whenever the ngrok container restarts.

---

## 5. Run

These start via the **root** compose, not from this folder:

```bash
cd ~/academy
docker compose up -d judge0-server judge0-worker judge0-db judge0-redis ml-runner
# or bring up the whole stack:
./start.sh
```

Verify:

```bash
docker compose ps            # all judge0-* and ml-runner Up / healthy
curl http://localhost:2358/about
```

---

## 6. Post-move checklist

- [ ] Repo cloned as `int-academy-platform-dependencies/` beside the other repos
- [ ] `judge0/` and `ml-runner/` populated (they're committed directly)
- [ ] Root `docker-compose.yml` paths point here (judge0.conf + ml-runner build)
- [ ] `judge0/judge0.conf` copied over (it's git-ignored — move it manually)
- [ ] `JUDGE0_AUTH_TOKEN` (root `.env`) matches `AUTHN/AUTHZ_TOKEN` (judge0.conf)
- [ ] Host allows `privileged` containers (Judge0)
- [ ] `judge0_data` / `ml_datasets` volumes restored if you need existing data
- [ ] `docker compose config` validates with no missing-file errors
- [ ] A test submission runs (Judge0) and a data cell runs (ml-runner)
