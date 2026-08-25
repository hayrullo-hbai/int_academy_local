# Contributing — Backend

---

## 1. Setup

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

You need PostgreSQL 16. Easiest is to borrow the compose one:

```bash
docker compose up -d db          # exposes 5433 on the host
# then in .env:
DATABASE_URL=postgresql://academy:academy@localhost:5433/academy_db
uvicorn app.main:app --reload --port 8000
```

Judge0 and ml-runner are optional; only submission endpoints need them. Set
`HSTAFF_ENABLED=false` to work fully offline — local login still works with the
seeded admin.

There is **no test suite and no linter configured**. Verification is manual, via
`/docs` or the frontend. Please keep changes small enough to exercise by hand.

---

## 2. House style

Match the code that is already there:

- Standard library → third party → `app.*` imports, blank-line separated.
- Type hints on function signatures; modern syntax (`str | None`, not `Optional`).
- Module docstrings explain *why the module exists*, not what each function does.
- Comments are reserved for non-obvious decisions — the SQL/stdin workaround in
  `judge0.py` is the model: it explains a constraint you could not infer.
- Errors from routes are `return _err("message", status)`, not raised. Messages
  are user-facing sentences, not codes.
- Services raise `AcademyError` / `PipelineError` / `ValueError`; routers catch
  and translate.

---

## 3. Adding a feature

### A new endpoint

1. Put the logic in `<domain>/services.py`, taking a `Session`.
2. Add Pydantic schemas (academy has `schemas.py`; other domains use `dict` bodies).
3. Add the route in `<domain>/router.py`:

```python
@router.post("/things")
def create_thing(data: ThingIn, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    if (err := _require_admin(user)) is not None:   # authorize FIRST
        return err
    try:
        thing = services.create_thing(db, data, owner_id=user.id)
    except services.AcademyError as exc:
        return _err(str(exc), 400)
    return _thing_out(thing, user)
```

**The authorization line is not optional and nothing will remind you.** There is
no default-deny. A route without a `_require_*` call is open to every
authenticated user.

### A new model

1. Define it in `<domain>/models.py`, inheriting `BaseModel` (UUID pk +
   timestamps).
2. If the module isn't already imported in `main.py`, import it there so the
   table registers on `Base.metadata`.
3. Restart — `create_all` creates the table.

### ⚠️ A new column on an EXISTING model

This is the single most common way to break a deployment.

`create_all` **only creates missing tables. It never alters existing ones.** Your
column will appear on a fresh dev database and be silently absent in production.

You must also add it to the migration list in `app/main.py`:

```python
_add_column_if_missing(
    "academy_problems", "time_limit_seconds",
    "time_limit_seconds INTEGER NOT NULL DEFAULT 10",
)
```

Rules for that DDL:

- Include a `DEFAULT` for `NOT NULL` columns — existing rows need a value.
- Write the column name twice: once as the check argument, once inside the DDL.
- Only `ADD COLUMN` is supported. Renames, type changes, drops and backfills
  need a manual SQL step against the deployed database.

### A new role or permission

See [roles.md §5](roles.md#5-adding-a-role). In short: edit
`rbac_catalog.py`, update the grouping sets, mirror in `frontend/lib/roles.ts`,
restart.

---

## 4. Security checklist

Run through this before opening a PR that touches routes or serialization.

- [ ] Every new route calls an authorization helper before doing work.
- [ ] Ownership-scoped routes use `_require_owner_or_admin`, not `_require_admin`.
- [ ] Nothing new leaks a `reference_solution`, `checker_code`, hidden test case,
      or MCQ `correct` flag. If you added such a field, update the matching
      stripper (see [architecture.md §5](architecture.md#5-ownership-and-secrets)).
- [ ] Content that shouldn't be *discoverable* returns **404, not 403** — this is
      how unpublished and private exams behave; match it.
- [ ] Role changes are mirrored in `frontend/lib/roles.ts`.
- [ ] Nothing new writes an uploaded file outside `MEDIA_ROOT` or serves user
      files from an authenticated path (`/media` is public).
- [ ] No secret, token or password lands in a log line or an error message.

---

## 5. Traps

**Refresh tokens are single-use.** Testing with two clients sharing a token will
log one of them out. That is correct behaviour, not a bug.

**`return _err(...)` bypasses `response_model`.** A route declaring
`response_model=ExamReadOut` can still return `{"detail": "..."}`. Don't rely on
the declared model to describe every response.

**Role names differ in meaning by module.** `MANAGEMENT_ROLES` is three roles in
`onboarding/access.py` and five in `identity/services.py`. Check the import.

**Cross-domain imports go inside functions.** Top-level imports between `identity`,
`onboarding` and `academy` will deadlock on circular imports. Follow the existing
pattern.

**Seeding deletes unknown system roles.** A role in the database with
`is_system=True` that is missing from `rbac_catalog.py` is removed on the next
boot, detaching its users. Locally created roles are safe.

**`list_pipelines` swallows every sync exception.** If sheet syncing appears
broken, the error is not being reported anywhere — check the sheet manually via
`POST /onboarding/sync`, which does surface errors.

**Sheet re-sync can clear a phone number.** `candidate_name` is guarded against
being overwritten with `None`; `candidate_phone` is not. A blank phone cell wipes
the stored one.

**Grading calls are synchronous and slow.** ml-runner has a 140-second timeout.
Do not add one to a request path a user waits on repeatedly.

---

## 6. Running the tests

```bash
# One-off: the suite needs its own database and refuses to touch any other.
docker compose up -d db
docker compose exec db psql -U academy -d postgres -c "CREATE DATABASE test_db OWNER academy;"

# Every run:
DATABASE_URL="postgresql://academy:academy@localhost:5433/test_db" pytest -q
```

`tests/conftest.py` asserts the database is named `test_db` or ends in `_test`
before it does anything else. That guard exists because the fixtures create and
drop tables: pointed at `academy_db` it would take your development data with
it. If you see `AssertionError: tests require a test database`, you forgot the
`DATABASE_URL` prefix — do not "fix" it by relaxing the assertion.

The suite is offline: no Judge0, no ml-runner, no hstaff, no Google Sheet.

---

## 7. Committing

- One logical change per commit; present-tense subject line ("Add exam invite
  revocation", not "added").
- Note explicitly in the PR description when a change requires a
  `_add_column_if_missing` entry, a new environment variable, or a matching
  frontend change.
- Update the relevant doc in `docs/` in the same commit. These files are the only
  description of the system's rules that exists.
