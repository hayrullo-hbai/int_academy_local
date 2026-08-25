# Backend System Design

The design decisions behind the code: why authentication works the way it does,
how grading is built, how the schema evolves, and where the system can fail.

---

## 1. Runtime topology

```
                    ┌──────────────────────────────────────────┐
   browser ────────▶│  frontend (Next.js :3000)                │
                    │  /api/* rewritten server-side            │
                    └───────────────┬──────────────────────────┘
                                    │  http://backend:8000
                                    ▼
                    ┌──────────────────────────────────────────┐
                    │  backend (FastAPI + gunicorn/uvicorn)    │
                    └──┬────────┬──────────┬─────────────┬─────┘
                       │        │          │             │
              ┌────────▼──┐ ┌───▼─────┐ ┌──▼────────┐ ┌──▼───────────┐
              │ Postgres  │ │ Judge0  │ │ ml-runner │ │ hstaff API   │
              │  :5432    │ │ :2358   │ │  :8080    │ │ (external)   │
              └───────────┘ └─────────┘ └───────────┘ └──────────────┘
                                                       ┌──────────────┐
                                                       │ Google Sheets│
                                                       └──────────────┘
              media_data volume → /app/media (datasets, proofs)
```

Because the frontend proxies `/api/*` through its own Next.js server, browser
requests are **same-origin** and CORS never applies in the Docker deployment.
CORS configuration only matters when a frontend is pointed directly at the
backend via `NEXT_PUBLIC_API_URL`.

---

## 2. Authentication

### The dual-source problem

Two populations must log in through one door:

- **Local users** — candidates who registered here and went through onboarding,
  plus locally created staff. Password hash lives in our `users` table.
- **hstaff users** — everyone already in the company HR system. Their password
  lives in hstaff and we must never store or see it.

### The solution: proxy-and-mirror

`login_user()` in `identity/services.py`:

The routing is by the stored `source` column, **not** by retry-on-failure:

```
POST /auth/login {email, password}
   │
   ├─ email exists AND source == "local"?
   │      └─ verify hash → ok: issue our tokens · fail: 401, STOP
   │         (a local account never falls through to hstaff)
   │
   └─ email unknown, or source == "hstaff" → HSTAFF_ENABLED?
            │
            ├─ POST hstaff /auth/login  (their credentials, never stored)
            │      ├─ 401/403 → HstaffAuthError → "Invalid email or password"
            │      └─ 200     → _mirror_hstaff_user()
            │                     • upsert local User with source="hstaff"
            │                     • upsert their roles from rbac_catalog
            │                     • cache hstaff access/refresh tokens on the row
            │                     • cache permission codenames from /permissions/me
            │                     • keep local full_name if name_customized
            │                          │
            └──────────────────────────┴──▶ issue OUR access + refresh tokens
```

**We always issue our own token.** Its `sub` is the local `User.id` regardless of
source, so every authorization check downstream reads local roles from one
place. hstaff's own tokens are stored on the user row only so the passthrough
proxy can act as that user later.

### Token design

| Token | Form | Lifetime | Storage |
|---|---|---|---|
| Access | JWT HS256, `sub` = local user UUID | 60 min | client only |
| Refresh | opaque `secrets.token_urlsafe(48)` | 7 days | SHA-256 hash in `refresh_tokens` |
| Password reset | opaque | short | SHA-256 hash in `password_reset_tokens` |

Refresh tokens are **single-use and rotated**: presenting one revokes it and
issues a new pair. This detects token theft (a stolen token used once makes the
legitimate one fail) but requires the client to serialize concurrent refreshes —
which is exactly why the frontend implements single-flight refresh.

Deactivating a user (`set_user_active(False)`) revokes all their live refresh
tokens, so sessions die at the next access-token expiry rather than lasting a
week.

### Why authorization is inline

There are no permission dependency factories. Each route calls a predicate:

```python
if (err := _require_admin(user)) is not None:
    return err
```

The tradeoff: it is verbose and easy to forget, but the rules are frequently
*relational* ("owner OR admin", "invited to this private exam", "assigned to
this stage") rather than a flat permission check, and expressing those as
dependencies would require loading the entity twice. The cost is that a missing
check is invisible — see [contributing.md](contributing.md) for the checklist.

---

## 3. Onboarding pipeline design

### The stage graph

Defined declaratively in `onboarding/enums.py`:

```
  intro_call    ─┐
  tech_interview ├─▶ discussion ─▶ payment ─▶ access ─▶ school tier granted
  culture_fit   ─┘
   (parallel)      (converges)    (proof +     (zoom +
                                   approval)    staff completes)
```

`is_unlocked()` encodes the only ordering rule: the three interviews are open
immediately, `discussion` unlocks when all three have passed, and each later
stage unlocks when its predecessor passed. Stage rows are materialised eagerly by
`ensure_stages()` so every pipeline always has exactly six.

### Sheet-driven, userless leads

Candidates originate in a Google Sheet (a Google Form's response tab).
`sync_from_sheet()` reads it and creates a pipeline per unseen email.

Column detection is heuristic, because the form's wording changes:
`_identity_columns()` first looks for a header containing "email", falls back to
sniffing which column *contains* email-shaped values, then takes the columns
immediately left and right as name and phone.

**Sync is strictly additive.** It creates pipelines and refreshes
name/phone; it never deletes. Changing the linked sheet replaces only the
`SheetSource` pointer row — every existing candidate, with all their stages,
reports, chats and decisions, survives untouched. The board therefore accumulates
across every sheet ever linked; the only pipelines that leave the view are those
with `status == "onboarded"` and those linked to staff accounts, both of which
are filtered by `list_pipelines()` rather than deleted.

`list_pipelines()` opportunistically syncs before listing, wrapped in a bare
`except: db.rollback()`. This makes the board self-updating but means a broken
sheet fails silently — see §7.

### Access model

Onboarding is deliberately narrow: only `academy-manager`, `hr` and `superadmin`
may see the board at all. Within that, `can_assign()` further restricts
interviewer assignment to `academy-manager`/`superadmin`, and
`INTERVIEW_ROLE_CAPS` restricts *who can be assigned to which interview* (HR owns
the intro call and culture fit; the academy manager owns the tech interview).

Chats have their own visibility: the `candidate` chat includes the candidate,
their interviewers and management; the `discussion` chat excludes the candidate
entirely.

---

## 4. Grading pipelines

Three independent engines, chosen by content type.

### 4.1 Judge0 — coding problems

Used for `python2`, `python3`, `sql`. `run_against_testcases()` submits every
test case as one **batch** with `expected_output`, then polls tokens until no
submission is in state 1 (queued) or 2 (processing).

Two non-obvious details:

- **SQL is special-cased.** sqlite3 reads the source file and ignores stdin, so
  per-test-case schema/data in `stdin` is *prepended to the source* instead.
- **Both cgroup flags are forced on.** `enable_per_process_and_thread_time_limit`
  and `..._memory_limit` make isolate run without cgroups, which is required on
  cgroup-v2 hosts.

Non-sample test cases have their `stdout`/`expected` nulled in the response so
students can't read hidden cases off a failing run.

`verify_reference_solution()` runs the examiner's own solution against their test
cases at authoring time and refuses to save a problem whose reference solution
doesn't pass — this catches trailing-whitespace and ordering mistakes before a
student ever sees them.

### 4.2 ml-runner — data problems and Data Lab

Data problems are Colab-style notebooks. The examiner authors ordered cells;
some code cells are marked `editable` with `points`, and their `source` holds the
hidden reference answer.

Grading assembles **one program** and runs it once:

```
  dataset preamble  (load_dataset() shim, dataset injected as base64)
+ cell 0 (examiner's code)
+ cell 1 (student's fill, or reference if ungraded)
+ ...
+ per-block stdout markers
+ checker_code  (hidden; asserts, raises on failure)
        │
        ▼
   POST ml-runner /run  →  {stdout, images, error}
        │
        ▼
   _parse_blocks(stdout) → per-block pass/fail → partial credit
```

Partial credit works by emitting sentinel markers around each editable block and
parsing them back out of stdout, so one execution grades every block. Points come
from each block's authored `points`; the score is the sum of passing blocks out
of the sum of all of them.

### 4.3 Output diffing — notebooks

A published notebook's graded cells are checked by running the *student's* cell
and the *reference* cell and comparing normalised output (`_output_matches`).
Cheaper than a checker script, and appropriate because notebooks are practice
rather than assessment.

### 4.4 Exam attempts

Attempts are **append-only**:

- While taking an exam, per-question Check/Run calls upsert into a *draft* row
  (`submitted=False`) so progress and live grading survive a refresh.
- Clicking "Submit exam" appends a new row with `submitted=True`. Prior drafts
  are preserved, so multiple submissions build a history rather than overwriting.

MCQ and code questions auto-grade. Text and datalab questions are stored
`graded=False, points_earned=0` for manual review — they have no auto-checker.

---

## 5. Schema management

**There is no migration tool.** On startup, `main.py`:

1. `Base.metadata.create_all(bind=engine)` — creates missing *tables*.
2. A list of `_add_column_if_missing(table, column, ddl)` calls — inspects the
   live table and issues `ALTER TABLE … ADD COLUMN` when absent.
3. `seed_admin()` → `seed_rbac()` — upserts the permission and role catalog and
   the superadmin account.

Every step is idempotent and safe to run on every boot, including with multiple
workers.

The tradeoff is deliberate — zero migration ceremony for a small team — but it
has hard limits:

- `create_all` **never alters an existing table**, so a new column on an existing
  model is invisible in production unless you also add it to the
  `_add_column_if_missing` list.
- There is no support for dropping columns, renaming, changing types, or
  backfilling. Those require a manual SQL step.
- There is no down-migration and no schema version record.

`seed_rbac()` additionally *removes* system roles that have disappeared from
`rbac_catalog.py`, detaching users from them first. Locally created roles
(`is_system=False`) are always preserved.

---

## 6. Media and file handling

Uploads go to `MEDIA_ROOT` under a per-purpose subdirectory
(`datasets/`, `payment_proofs/`, `address_proofs/`), renamed to a random UUID
with the original suffix preserved. `main.py` mounts the whole directory as
static at `/media`.

The consequence: **anything uploaded is publicly readable by URL.** The filenames
are unguessable UUIDs, which is security-by-obscurity, not access control.
Payment and address proofs are personal documents; treat the URLs as secrets and
do not put them anywhere shareable. The directory is backed by a named Docker
volume so uploads survive rebuilds.

---

## 7. Known failure modes

| Failure | Current behaviour | Impact |
|---|---|---|
| Sheet unreadable during `list_pipelines` | caught by a bare `except`, rolled back, board renders stale | Silent. New leads stop appearing with no error shown. |
| Sheet has no detectable email column | `sync_from_sheet` raises `PipelineError`, swallowed as above | Same — only the one-time warning at link time surfaces it. |
| Judge0 down | `requests` raises, propagates as 500 | Submission endpoints fail; the rest of the app is fine. |
| ml-runner down / timeout | `MLRUNNER_TIMEOUT` (140s) then raises | A student can wait 140s before seeing an error. |
| hstaff down | `HstaffError`; login falls back to failure | hstaff users cannot log in. Local users unaffected. |
| Concurrent refresh from one client | first rotates, second sees a revoked token → 401 → logout | Mitigated only by the frontend's single-flight refresh. |
| New column not added to the migration list | column missing in production, queries error | Silent in dev (fresh DB gets it via `create_all`), breaks on deploy. |
| Phone blank in a re-synced sheet row | `pipeline.candidate_phone` overwritten with `None` | Existing phone data is lost. The name field is guarded against this; phone is not. |

---

## 8. Scaling notes

The current design targets a single-instance deployment for one company's
academy. Where it would need work:

- **Grading is synchronous.** A submission holds an HTTP connection for the whole
  Judge0/ml-runner round trip. Under load this needs a queue and a results
  endpoint.
- **Progress endpoints load full submission history** per user and aggregate in
  Python. Fine for hundreds of submissions, not for millions.
- **`list_pipelines` syncs the sheet on every board load** — an external HTTP
  call in the read path, and the board polls on an interval.
- **Startup migrations race** if many workers boot simultaneously against a
  brand-new database. `create_all` and the `ALTER` guards are individually safe
  but not transactionally coordinated.
