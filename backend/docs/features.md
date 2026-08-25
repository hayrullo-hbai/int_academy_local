# Backend Features

Every feature the backend provides: what it is for, which endpoints implement it,
and exactly who may do what. Role names are defined in
[roles.md](roles.md).

Shorthand used throughout:

- **admin** — `superadmin`, or any user with `is_superuser`
- **management** — `academy-manager`, `hr`, `department-head`, `c-level`, `superadmin`
- **onboarding staff** — `academy-manager`, `hr`, `superadmin` (nobody else, ever)
- **academy admin** — `academy-manager`, `superadmin`, `examiner`
- **learner** — `school`, `softlanding`, `foundation`, `talent`
- **owner** — the user whose `owner_id`/`uploaded_by_id` is on the record

---

## 1. Authentication

**Purpose.** Let two separate user populations — people who exist only here, and
people who exist in the company's hstaff HR system — log in through one form,
and give the rest of the application a single identity to reason about.

**How.** The stored `source` column decides which path a login takes. A known
`source="local"` account is verified against the local hash and, if that fails,
is rejected outright — it never falls through to hstaff. An unknown email or a
known `source="hstaff"` account is proxied to hstaff's public `/auth/login`; on
success the user is mirrored locally with `source="hstaff"` and their hstaff
tokens and permissions cached on the row. Either path ends with *our* access +
refresh token pair.

| Endpoint | Purpose | Who |
|---|---|---|
| `POST /auth/login` | Log in (local or hstaff) | anyone |
| `POST /auth/refresh` | Rotate a refresh token for a new pair | anyone holding a valid refresh token |
| `POST /auth/logout` | Revoke the presented refresh token | anyone |
| `POST /auth/forgot-password` | Email a reset link | anyone — **local users only**; always returns the same message so account existence isn't leaked |
| `POST /auth/reset-password` | Consume a reset token, set a new password | anyone holding a valid token |
| `GET /auth/me` | Current identity, roles, permissions | any authenticated user |

Notes that matter operationally:

- Refresh tokens are **single-use**. Concurrent refreshes will revoke each other;
  clients must serialize them.
- A password reset also **revokes every live refresh token** for that user.
- hstaff users have no local password, so forgot-password deliberately ignores
  them — they reset through hstaff.
- The reset link is currently **written to the application log**, not emailed.
  There is no SMTP backend wired up.

---

## 2. Self-service profile

**Purpose.** Let any user maintain the details the platform needs from them —
display name, address (for contracts/logistics), and proof of address.

| Endpoint | Purpose | Who |
|---|---|---|
| `PATCH /auth/me/profile` | Change own display name | any authenticated user |
| `PATCH /auth/me/address` | Set own address and office location | any authenticated user |
| `POST /auth/me/address-proof` | Upload an address-proof document | any authenticated user |
| `PATCH /auth/me/active-role` | Switch which granted role is active | any authenticated user, among roles they already hold |
| `GET /profile/{email}` | View a public profile | anyone, including anonymous; superadmin profiles are hidden (404) |

Editing your own name sets `name_customized=True`, which permanently protects it
from being overwritten by a later hstaff re-sync. Office location is validated
against the `OfficeLocation` enum (Namangan, Tashkent, Incheon) and silently
ignored if invalid.

Address *verification* is not self-service — see §5.

`GET /profile/{email}` accepts either a full email or just the local part
(`firdavs` matches `firdavs@…`), which is what makes the frontend's
`/profile/<localpart>` URLs work.

---

## 3. Portfolio profile — skills, projects, certificates, accounts

**Purpose.** The self-service profile above covers what the *platform* needs
from a user. This module covers what a user wants to *show*: a portfolio of
claims that a reviewer can verify and a manager can publish.

Four item types share one lifecycle: `skill`, `project`, `certificate`,
`social_account`.

### The items

| Endpoint | Purpose | Who |
|---|---|---|
| `GET /profile/meta/vocabularies` | Skill categories, evidence kinds, platforms, section labels | any authenticated user |
| `GET /profile/{ident}/skills` | List a profile's skills | anyone who passes the visibility rules |
| `POST\|PATCH\|DELETE /profile/me/skills[/{id}]` | Maintain own skills | owner, or a curator overriding |
| `PUT\|DELETE /profile/me/skills/{id}/attachment` | Attach or drop supporting evidence (image or PDF, 10MB) | owner, or a curator overriding |
| `GET /profile/{ident}/projects` | List a profile's projects | visibility rules |
| `POST\|PATCH\|DELETE /profile/me/projects[/{id}]` | Maintain own projects | owner, or a curator overriding |
| `GET /profile/{ident}/certificates` | List a profile's certificates | visibility rules |
| `POST /profile/me/certificates` | Upload a certificate — **image only** (PNG/JPEG/GIF/WebP), max 10MB, plus a title, in one multipart request | owner, or a curator overriding |
| `PATCH /profile/me/certificates/{id}` | Rename / re-slug | owner, or a curator overriding |
| `PUT /profile/me/certificates/{id}/file` | Swap the document; counts as an edit, so verification resets | owner, or a curator overriding |
| `GET /profile/certificates/{id}/download` | Download the file | anyone allowed to see the item |
| `GET /profile/{ident}/social-accounts` | List linked GitHub / LinkedIn / Discord | visibility rules |
| `POST\|PATCH\|DELETE /profile/me/social-accounts[/{id}]` | Maintain own links | owner, or a curator overriding |

A certificate is created with its file — the two arrive in one multipart POST,
because a certificate without its document has nothing to review. Certificates
accept images only: they are rendered as a 16:9 preview wherever they appear,
and a PDF has no thumbnail to put there. Skill evidence still accepts PDFs.

### The verification workflow

Every item carries a `ReviewState`: `draft` → `submitted` → `under_review` →
`verified` or `rejected`.

`social_account` is the exception: it's saved already `verified`, with no
mentor step. hstaff already tracks `github_username` / `linkedin_username` /
`discord_username` on the talent profile, so a link only ever needed an
ownership check — and hstaff is a better source for that than a mentor
clicking approve. Listing a profile's social accounts auto-imports whatever
hstaff already has on file for platforms not yet linked locally, and
saving/editing a link here pushes it back to hstaff under the owner's own
token, keeping the two in sync (see `app/profile/hstaff_sync.py`).

| Endpoint | Purpose | Who |
|---|---|---|
| `POST /profile/me/{item_type}/{item_id}/submit` | Send an item for verification | owner |
| `GET /profile/me/{item_type}/{item_id}/history` | That item's own review history | owner |
| `GET /profile/reviews/queue` | Everything awaiting verification | mentors and curators |
| `POST /profile/reviews/{item_type}/{item_id}/start` | Claim an item for review | mentors and curators |
| `POST /profile/reviews/{item_type}/{item_id}/approve` | Verify the claim | mentors and curators |
| `POST /profile/reviews/{item_type}/{item_id}/reject` | Reject with a reason | mentors and curators |

Editing a verified item sends it back for review, and until it is approved again
the previously approved version is what other people see. That is why the
certificate file swap is a separate endpoint: replacing the document is an edit,
not a rename.

### Visibility and publication

Two independent layers, and they are easy to confuse:

- **Per item** — `private`, `mentor_only`, `hr_only`, `academy_only`,
  `public_summary`, `public`. Owners may set only the first four; the two public
  levels are reserved for Academy Managers and Admins.
- **Per section** — an Academy Manager curates which *sections* of a profile
  (bio, skills, projects, languages, certifications, activity, analytics,
  external accounts) are exposed publicly. Curation is never per item. Every
  section starts unshared.

| Endpoint | Purpose | Who |
|---|---|---|
| `GET\|PUT /profile/{ident}/share` | Read / set which sections are published | read: any authenticated user · write: Academy Manager or Admin, never the owner |
| `GET /profile/{ident}/share/audit` | Who changed publication, and when | Academy Manager or Admin |
| `POST /profile/reviews/{item_type}/{item_id}/override-visibility` | Staff override of one item's visibility, with a required reason | Academy Manager or Admin |
| `GET /profile/me/{item_type}/{item_id}/visibility-audit` | Per-item visibility trail | owner |

The audit trail is append-only. An ordinary edit records `changed`; the
dedicated override endpoints record `overridden`, require a reason, and notify
the owner. A later `changed` row after an `overridden` one is expected and is
never collapsed — nothing is a silent reversion.

> **Note.** Item visibility gates the API, not the file bytes. Uploaded
> certificates and skill evidence are also reachable at their raw `/media/...`
> URL with no authentication at all — see the operational note in the README.

---

## 4. Roles, permissions and user administration

**Purpose.** Control who can do what, mirroring hstaff's role hierarchy so a
person's authority is consistent across both systems, while still allowing
locally defined roles like `examiner`.

### Reading

| Endpoint | Purpose | Who |
|---|---|---|
| `GET /roles` | List roles and their permissions | any authenticated user (superadmin role itself is hidden) |
| `GET /permissions` | List the permission catalog | any authenticated user |
| `GET /users` | List all users | **any authenticated user** — the directory is intentionally open; superadmins are filtered out |

### Writing

| Endpoint | Purpose | Who |
|---|---|---|
| `POST /users/create` | Create a local user | `superadmin`, `academy-manager` — but an academy-manager who is not elevated is then rejected, so in practice superadmin |
| `PATCH /users/{uid}/roles` | Set a user's roles and primary role | `superadmin`, `academy-manager` |
| `PATCH /users/{uid}` | Activate / deactivate a user | `superadmin`, `academy-manager` |
| `DELETE /users/{uid}/delete` | Delete a user | `superadmin`, `academy-manager` |
| `POST /roles` | Create a local role | management |
| `PUT /roles/{rid}` | Edit a role | management |
| `DELETE /roles/{rid}` | Delete a role | management — **system roles cannot be deleted** |

### The guard rails on role assignment

These are enforced in `identity/services.py` and are the most security-sensitive
logic in the codebase:

- The `superadmin` role **can never be assigned** through the API, by anyone.
- Elevated roles (`superadmin`, `c-level`, `advisor`, `cfo`) may only be granted
  by an already-elevated user.
- Executive roles (`c-level`) may only be granted or revoked by a superadmin or a
  sitting C-level.
- A non-elevated user **cannot modify, deactivate or delete an elevated user** at
  all.
- Nobody can delete their own account.
- Deactivating a user immediately revokes their refresh tokens.

Newly created local users get `must_change_password=True`.

---

## 5. Onboarding pipeline

**Purpose.** Run a candidate from "submitted the application form" to "has
platform access and the `school` role", with every interview, decision and
conversation recorded on-platform for audit.

**Access rule for the whole feature: onboarding staff only** —
`academy-manager`, `hr`, `superadmin`. No other role can see any part of the
board, including examiners and C-level. The single exception is the candidate's
own view (§5.1).

### The six stages

| # | Stage | Kind | What happens | Who may act |
|---|---|---|---|---|
| 1 | `intro_call` | interview | 5–15 min screening call, zoom link + written report | assigned `hr` (or superadmin) |
| 2 | `tech_interview` | interview | Technical interview, zoom + report | assigned `academy-manager` (or superadmin) |
| 3 | `culture_fit` | interview | Culture-fit interview, zoom + report | assigned `hr` (or superadmin) |
| 4 | `discussion` | discussion | Interviewers converge on a decision, zoom + report | discussion participants |
| 5 | `payment` | payment | Candidate uploads proof, management approves | management |
| 6 | `access` | access | Platform/doc handover; completing it grants the `school` role | onboarding staff |

Stages 1–3 run in parallel and are unlocked immediately. Stage 4 unlocks when all
three have passed; 5 and 6 unlock in sequence. A failed stage sets the pipeline
to `rejected` — the record stays on the board, it is not deleted.

### 5.1 Candidate's own view

| Endpoint | Purpose | Who |
|---|---|---|
| `GET /onboarding/me` | Own pipeline state | the candidate |
| `GET /onboarding/me/chat` | Own candidate-chat thread | the candidate |
| `POST /onboarding/me/chat` | Post to own thread | the candidate |
| `POST /onboarding/me/payment-proof` | Upload payment proof | the candidate |

The candidate sees stage progress and their own chat. They never see the
discussion chat, interviewer reports, or other candidates.

### 5.2 Staff board

| Endpoint | Purpose | Who |
|---|---|---|
| `GET /onboarding/candidates` | The board (also triggers a sheet sync) | onboarding staff |
| `GET /onboarding/candidates/{pid}` | One candidate's full pipeline | onboarding staff |
| `PATCH /onboarding/candidates/{pid}/row` | Inline-edit a board row | management |
| `GET /onboarding/interviewers` | Assignable interviewers | onboarding staff |
| `GET /onboarding/candidates/{pid}/stages/{key}/eligible` | Who may take this stage | onboarding staff |
| `POST /onboarding/candidates/{pid}/stages/{key}/assign` | Assign interviewers | `academy-manager`, `superadmin` — **HR may not assign** |
| `PATCH /onboarding/candidates/{pid}/stages/{key}/zoom` | Set the zoom link | assignee or management |
| `POST /onboarding/candidates/{pid}/stages/{key}/report` | Submit report + pass/fail | the assigned interviewer |
| `POST /onboarding/candidates/{pid}/payment/decision` | Approve/reject payment | management |
| `POST /onboarding/candidates/{pid}/access/complete` | Complete access, grant `school` | onboarding staff |
| `POST /onboarding/candidates/{pid}/address/verify` | Mark address verified | management |

### 5.3 Chats

Two threads per pipeline:

- **`candidate`** — candidate + their interviewers + management.
- **`discussion`** — interviewers + management only. The candidate can never see
  or reach it.

| Endpoint | Purpose | Who |
|---|---|---|
| `GET /onboarding/candidates/{pid}/chats/{kind}` | Read a thread | participants of that thread |
| `POST /onboarding/candidates/{pid}/chats/{kind}` | Post a message | participants of that thread |

### 5.4 Google Sheet source

| Endpoint | Purpose | Who |
|---|---|---|
| `GET /onboarding/sheet` | Raw preview of the linked sheet | onboarding staff |
| `GET /onboarding/sheet/source` | Which sheet is linked | onboarding staff |
| `POST /onboarding/sheet/source` | Link a different sheet | management |
| `POST /onboarding/sync` | Force a sync now | management |

**Changing the linked sheet does not delete any candidate.** Only the pointer row
is replaced; sync is additive and never removes pipelines. See
[system-design.md §3](system-design.md#3-onboarding-pipeline-design).

---

## 6. Academy — coding problems

**Purpose.** A shared bank of algorithmic problems in Python 2, Python 3 and SQL,
auto-graded against test cases by Judge0.

**Who can open the Academy at all:** learners, `examiner`, academy admins. Anyone
else gets 403 on every academy endpoint.

| Endpoint | Purpose | Who |
|---|---|---|
| `GET /academy/problems` | List problems | academy access |
| `GET /academy/problems/{slug}` | Problem detail | academy access — reference solution and hidden test cases are stripped unless owner/admin |
| `POST /academy/problems` | Create a problem | academy admin |
| `PUT /academy/problems/{slug}` | Edit a problem | owner or academy admin |
| `DELETE /academy/problems/{slug}` | Delete a problem | owner or academy admin |
| `POST /academy/problems/{slug}/submit` | Submit a solution | academy access |
| `POST /academy/run` | Run code without grading (playground) | academy access |
| `GET /academy/submissions` | Own submission history | academy access |
| `GET /academy/submissions/{id}` | One submission | owner of the submission |
| `DELETE /academy/submissions/{id}` | Delete a submission | owner |

On create/update, the examiner's own reference solution is executed against the
test cases and the write is **rejected if it doesn't pass** — this catches
whitespace and ordering mistakes at authoring time. Only test cases flagged
`is_sample` have their expected output revealed to students.

---

## 7. Academy — datasets

**Purpose.** CSV files that data work runs against, referenced by slug in code
via `load_dataset("...")`.

| Endpoint | Purpose | Who |
|---|---|---|
| `GET /academy/datasets` | List datasets | academy access |
| `GET /academy/datasets/{slug}` | Detail + column names | academy access |
| `POST /academy/datasets` | Upload a CSV | academy admin |
| `DELETE /academy/datasets/{slug}` | Delete | uploader or academy admin |

Files land in `MEDIA_ROOT/datasets/` under a random UUID name and are served
statically at `/media/…`.

---

## 8. Academy — Data Lab

**Purpose.** A free-form scratch environment: write pandas/matplotlib code
against a dataset and see stdout plus rendered plots. Nothing is graded or
stored — it's for exploration and for examiners prototyping problems.

| Endpoint | Purpose | Who |
|---|---|---|
| `POST /academy/ml-run` | Execute code against a dataset | academy access |

Execution goes to ml-runner with the dataset injected; the response carries
stdout, any error, and base64 images.

---

## 9. Academy — notebooks

**Purpose.** Saved, reopenable, cell-based workspaces. A user builds a notebook
of markdown + code cells; an author can then **publish** it so learners can solve
it, with graded cells checked by comparing the learner's cell output against the
reference cell's output.

| Endpoint | Purpose | Who |
|---|---|---|
| `GET /academy/notebooks` | List notebooks | academy access — own notebooks plus published ones |
| `POST /academy/notebooks` | Create | academy access (anyone may keep notebooks) |
| `GET /academy/notebooks/{slug}` | Open | owner, or anyone if published; solution cells stripped for non-authors |
| `PUT /academy/notebooks/{slug}` | Edit | owner or academy admin |
| `DELETE /academy/notebooks/{slug}` | Delete | owner or academy admin |
| `POST /academy/notebooks/run-cell` | Run one cell | academy access |
| `POST /academy/notebooks/check-cell` | Check one cell against the reference | academy access |
| `POST /academy/notebooks/{slug}/submit` | Submit an attempt | academy access |
| `GET /academy/notebooks/{slug}/submissions` | Submission list | author sees everyone's; a learner sees only their own |
| `GET /academy/notebooks/{slug}/submissions/{id}` | One submission | author, or the submitting user |

Notebooks are **private by default** (`is_public=False`). Publishing is what
turns a personal workspace into a solvable exercise.

---

## 10. Academy — data problems

**Purpose.** Graded Colab-style notebooks. The examiner writes the surrounding
code and marks specific cells editable with point values; the student fills the
blanks. Everything is concatenated into one program, run once by ml-runner, and a
hidden checker asserts correctness — with **partial credit** per editable block.

| Endpoint | Purpose | Who |
|---|---|---|
| `GET /academy/data-problems` (via list) | Browse | academy access |
| Create / edit / delete | Author content | academy admin; edit/delete also allowed for the owner |
| Submit | Attempt the problem | academy access |
| `POST /academy/questions/{id}/check-block` | Check one editable block | academy access |

Students never receive the reference `source` of editable cells or the
`checker_code` — both are stripped by `student_cells()` before serialization.
They see only the `example` hint and the block's point value.

---

## 11. Academy — exams

**Purpose.** Assessments combining four question types — multiple choice, free
text, an inline coding problem, and a data-lab/data-problem task — with attempt
history and an examiner review view.

### Visibility

An exam is visible to a user if **any** of these hold:

1. they own it, or they are an academy admin; or
2. it is **published** and `access_type == "public"`; or
3. it is **published**, `access_type == "private"`, and their email is on the
   invite list.

Otherwise every endpoint answers 404 — not 403 — so unpublished and private exams
are not discoverable.

| Endpoint | Purpose | Who |
|---|---|---|
| `GET /academy/exams` | List visible exams | academy access |
| `GET /academy/exams/{slug}` | Open an exam | per the visibility rule; correct MCQ answers included only for owner/admin |
| `POST /academy/exams` | Create | academy admin |
| `PUT /academy/exams/{slug}` | Edit | owner or academy admin |
| `DELETE /academy/exams/{slug}` | Delete | owner or academy admin |
| `POST /academy/exams/{slug}/invites` | Invite emails to a private exam | owner or academy admin |
| `DELETE /academy/exams/{slug}/invites` | Revoke an invite | owner or academy admin |
| `GET /academy/users/search` | Find users to invite | academy admin |
| `POST /academy/questions/{id}/check` | Check an MCQ answer live | academy access, if the exam is visible |
| `POST /academy/questions/{id}/answer` | Save an answer to the draft attempt | academy access |
| `POST /academy/questions/{id}/exam-problem/submit` | Submit code for a coding question | academy access |
| `POST /academy/questions/{id}/data-problem/check` | Check a data-problem question | academy access |
| `POST /academy/exams/{slug}/submit` | Submit the exam | academy access |
| `GET /academy/exams/{slug}/attempt` | Own attempt | the attempting user |
| `GET /academy/exams/{slug}/submissions` | **All** attempts, with reference solutions attached | owner or academy admin |

Attempts are append-only: answering questions updates a draft row
(`submitted=False`); submitting appends a new `submitted=True` row, preserving
history. MCQ and code questions auto-grade; text and datalab questions are stored
ungraded for manual review.

---

## 12. Academy — progress and visibility

**Purpose.** A per-user record of solved problems, data problems and exam results
— usable as a portfolio, and as the examiner's view of how someone is doing.

| Endpoint | Purpose | Who |
|---|---|---|
| `PATCH /academy/me/visibility` | Make own progress public/private | any authenticated user |
| `GET /academy/progress/{email}` | A user's progress | the owner and academy admins always; other logged-in users only if the owner made it public (403 otherwise) |

Per-question exam breakdowns are additionally restricted: only the owner and
academy admins see them, even on a public profile.

---

## 13. hstaff integration

**Purpose.** Reuse the company HR system as the source of truth for staff
accounts, profiles and talent data, instead of duplicating it.

| Endpoint | Purpose | Who |
|---|---|---|
| `POST /hstaff/users/create` | Register a user on hstaff via the HR service account | `superadmin`, `hr`, `academy-manager`, or anyone with `users:create`. `c-level`/`superadmin` types are rejected up front. |
| `POST /hstaff/users/sync` | Bulk-mirror all hstaff users locally | same as above |
| `GET /hstaff/users/{email}/bundle` | Full talent bundle for any user, via the service account | see the endpoint's own docstring |
| `GET /hstaff/profile` | Own cached hstaff profile | hstaff-linked users only |
| `GET /hstaff/analytics` | Aggregated hstaff analytics | hstaff-linked users only |
| `GET /hstaff/api/{path}` | Generic read passthrough | hstaff-linked users only |
| `POST\|PUT\|PATCH\|DELETE /hstaff/api/{path}` | Generic write passthrough (own profile, skills, languages, projects, avatar) | hstaff-linked users only |

The passthrough forwards **under the user's own hstaff token**, so hstaff's RBAC
— not ours — decides what the request is allowed to do. A 401 triggers one
transparent token refresh and retry. Multipart uploads are rebuilt so avatar
files reach hstaff intact.

Users created here get a verification email from hstaff and set their own
password there; we never handle it.

---

## 14. Operational endpoints

| Endpoint | Purpose | Who |
|---|---|---|
| `GET /health` | Liveness check | anyone |
| `GET /docs`, `/openapi.json` | Interactive API documentation | anyone |
| `GET /media/{path}` | Uploaded files: datasets, payment and address proofs | **anyone with the URL** — no authentication |

The `/media` mount is unauthenticated. Filenames are random UUIDs, which makes
them unguessable but not access-controlled. Payment and address proofs are
personal documents; treat their URLs as secrets.
