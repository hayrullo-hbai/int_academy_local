# Backend Architecture

How the code is organised, how a request flows through it, and what the data
model looks like.

---

## 1. Layering

Every domain package uses the same four-file shape:

```
<domain>/
├── models.py     SQLAlchemy ORM models — the only place tables are defined
├── services.py   Business logic, grading, external calls. Takes a Session.
├── router.py     HTTP surface: parse, authorize, delegate, serialize
├── access.py     Pure role predicates (no DB, no HTTP)
└── enums.py      Constants, status values, static pipeline definitions
```

The rules that hold across all of them:

- **Routers never contain business logic.** They authenticate (via `Depends`),
  authorize (via an inline `access.py` helper), call a service, and shape the
  response. `academy/router.py` is the largest at ~950 lines, but it is almost
  entirely gating and serialization.
- **Services never touch `Request`/`Response`.** They take a `Session` and plain
  data, and raise domain errors (`AcademyError`, `PipelineError`, `ValueError`)
  that routers translate into HTTP status codes.
- **`access.py` predicates are pure functions of a user object.** They take a
  user, return a bool, and never query. This is what makes them cheap to call
  inline in every route.
- **Cross-domain imports are done inside functions**, not at module top level, to
  avoid circular imports (e.g. `onboarding/services.py` imports `identity.models`
  inside `sync_from_sheet`).

---

## 2. Package map

```
app/
├── main.py       App assembly. CORS, validation-error humanizer,
│                 startup schema creation + column migrations + seeding,
│                 /health, /media static mount, router registration.
├── seed.py       seed_rbac() + seed_admin(), both idempotent.
│
├── core/
│   ├── config.py     Settings singleton, read from env. Validates prod config.
│   ├── database.py   Engine, SessionLocal, Base, BaseModel (uuid pk +
│   │                 created_at/updated_at), get_db() dependency.
│   ├── security.py   Password hashing, JWT encode/decode, opaque token helpers.
│   ├── deps.py       get_current_user / get_optional_user. Authentication only.
│   ├── files.py      save_upload() into MEDIA_ROOT, media_absolute_url().
│   └── responses.py  error(detail, status) → the {"detail": ...} shape.
│
├── identity/     Users, Role, Permission, RefreshToken, PasswordResetToken.
│                 Login (both sources), refresh rotation, password reset,
│                 role/user administration, rbac_catalog.py (the hstaff mirror).
│
├── onboarding/   OnboardingPipeline, Stage, StageReport, Chat, ChatMessage,
│                 SheetSource. Stage graph in enums.py, Google Sheet reader in
│                 gsheet.py, all pipeline rules in services.py.
│
├── academy/      Problem/TestCase, Dataset, Notebook/NotebookSubmission,
│                 DataProblem/DataSubmission, Exam/Question/ExamAttempt and the
│                 exam-local ExamProblem family. judge0.py and mlrunner.py are
│                 the two execution backends. schemas.py holds all Pydantic I/O.
│
└── hstaff/       client.py (HTTP client with service-account token caching),
                  router.py (typed endpoints + generic passthrough proxy).
```

---

## 3. Request lifecycle

```
HTTP request
   │
   ├─ CORSMiddleware                       main.py
   │
   ├─ Depends(get_current_user)            core/deps.py
   │     Bearer token → decode HS256 → sub is our local User UUID
   │     → db.get(User, uid), reject if missing or inactive
   │
   ├─ Depends(get_db)                      core/database.py — session per request
   │
   ├─ route function                       <domain>/router.py
   │     1. inline authorization:  if (err := _require_admin(user)): return err
   │     2. load entity, 404 if absent
   │     3. call service
   │     4. serialize (Pydantic response_model, or a hand-built dict)
   │
   └─ error paths
         RequestValidationError → validation_error_handler collapses pydantic's
             dump into one readable sentence, keeping {"detail": str}
         Domain error           → caught in the router, returned via _err()
```

Two consequences worth internalising:

- The token's `sub` is **always our local user id**, even for hstaff-sourced
  users. Downstream code never has to know where a user came from.
- Because errors are `return`ed as `JSONResponse` rather than raised, a route
  declaring `response_model=ExamReadOut` can still return a `{"detail": ...}`
  body with a 403. FastAPI does not validate the returned `Response`.

---

## 4. Data model

### Identity

```
Permission ──< role_permissions >── Role ──< user_roles >── User
                                     │                       │
                                     └── created_by ─────────┘
                                                  User.role_id → Role  (primary/active role)

User ──< RefreshToken          (SHA-256 hashes, single-use, rotated)
     └─< PasswordResetToken    (SHA-256 hashes, single-use)
```

A user has a *set* of granted roles (`user_roles`) plus one *active/primary*
role (`role_id`). `User.role_names` unions both, so the active role always
counts as granted. `User.permission_codenames` returns hstaff's cached
permissions for hstaff users, and the union of role permissions for local ones.

### Onboarding

```
OnboardingPipeline ──< Stage ──< StageReport
      │                  └──< stage_assignees >── User
      ├──< Chat ──< ChatMessage
      └── user_id → User   (nullable! see below)

SheetSource   (which spreadsheet the board reads; effectively a singleton)
```

`user_id` is nullable because a pipeline starts life as a *userless* lead pulled
from the Google Sheet, identified only by `candidate_email` / `candidate_name` /
`candidate_phone`. It is adopted onto a real `User` when that person first
authenticates. The `email` / `display_name` / `phone` properties resolve to the
user when linked and the raw candidate fields otherwise.

### Academy

```
Problem ──< TestCase                      shared coding-problem bank
   └──< Submission → User

Dataset                                    admin-uploaded CSV
   ├──< DataProblem ──< DataSubmission → User
   └──< Notebook    ──< NotebookSubmission → User

Exam ──< Question ──┬── ExamProblem ──< ExamProblemTestCase
     │              │        └──< ExamSubmission
     │              └── (mcq choices / datalab / copied data-problem cells)
     ├──< ExamInvite            private-exam allowlist, by email
     └──< ExamAttempt → User    append-only attempt history
```

The deliberate design choice here: **exam content is copied, never linked.**
When an examiner attaches a coding problem to a question, the problem is copied
into `ExamProblem` (a separate table with its own test cases and submissions)
rather than pointing at the shared `Problem` bank. Same for data problems, whose
cells are copied into `Question.data_problem_cells`. This means:

- editing the shared bank never silently changes a live exam,
- deleting an exam cleanly deletes everything it owns,
- exam submissions never pollute the shared submission history.

---

## 5. Ownership and secrets

Two orthogonal ideas run through the academy package:

**Ownership.** `Problem`, `Dataset`, `Notebook`, `DataProblem` and `Exam` all
carry a nullable `owner_id`/`uploaded_by_id`. `_require_owner_or_admin()` lets an
author manage their own content without holding an admin role.

**Secret stripping.** Reference solutions, hidden test cases and `checker_code`
must never reach a student. This is enforced at serialization time, in one place
per entity:

| Entity | Stripper | Hides |
|---|---|---|
| Problem | `_problem_out()` in router | `reference_solution`, non-sample test cases |
| DataProblem | `student_cells()` in services | reference `source` of editable cells, `checker_code` |
| Notebook | `_strip_solutions()` in services | solution cells |
| Question | `question_read_payload(include_correct=False)` | which MCQ choices are correct |

If you add a field holding an answer, add it to the matching stripper.

---

## 6. External dependencies

| Service | Client | Used by | Failure behaviour |
|---|---|---|---|
| PostgreSQL | SQLAlchemy | everything | fatal |
| Judge0 | `academy/judge0.py` | coding problems, exam code questions | endpoint 5xx |
| ml-runner | `academy/mlrunner.py` | Data Lab, data problems, notebooks | endpoint 5xx |
| Google Sheets | `onboarding/gsheet.py` | lead sync | `SheetError` → 502; sync inside `list_pipelines` is swallowed |
| hstaff API | `hstaff/client.py` | login fallback, profile, analytics, passthrough | `HstaffError`; disable with `HSTAFF_ENABLED=false` |

All are synchronous `requests` calls made from `def` (not `async def`) route
handlers, so FastAPI runs them in a threadpool and they do not block the event
loop.
