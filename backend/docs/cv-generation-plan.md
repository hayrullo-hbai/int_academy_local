# CV Generation from Approved Profile Data — Implementation Plan

## Status

IMPLEMENTED — the changes described below have been merged into the codebase.

## What we are building

Right now the CV generator reads from hstaff-mirror tables (`TalentSkill`,
`TalentProject`, etc.). We will make it read from the local **profile** tables
(`ProfileSkill`, `ProfileProject`, `ProfileSocialAccount`) that go through the
academy's own review workflow.

A skill or project only appears in a CV after a mentor or admin has approved it.
Unverified, hidden, or confidential items never leak out.

## How the profile workflow works today

```
Draft → Submitted → Under Review → Verified → (published by Manager/Admin)
```

- `approved_snapshot` keeps the last approved version.
- `ProfileShare` decides which sections are public.
- Projects have confidentiality levels: `public`, `public_summary`,
  `academy_only`, `internal`, `client_confidential`.

## What will change

### 1. New CV snapshot builder (`app/talent/services.py`)

A function called `build_profile_cv_snapshot(...)` will collect only allowed
profile data.

| Section | Source | Allowed in CV? |
|---|---|---|
| Bio / full name | `User.full_name`, `User.hstaff_profile.bio` | Always |
| Email / phone | `User` | Always |
| Social links | Verified `ProfileSocialAccount` | Only if `verified` |
| Skills | `ProfileSkill` | Only if `verified` |
| Projects | `ProfileProject` | Only if `verified` AND not `internal` / `client_confidential` |
| Languages | `TalentLanguage` | Only if `verified` and not `hidden` |
| Experience | `TalentExperience` | Only if `verified` and not `hidden` |
| Certificates | `TalentCertificate` | Only if `verified` and not `hidden` |
| Academy progress | `User.academy_progress_public` | Only if the user made it public |

**Project confidentiality rules:**

- `public` → full details.
- `public_summary` → only the approved public summary.
- `internal` or `client_confidential` → excluded completely.

### 2. New CV generator (`app/talent/services.py`)

`generate_profile_cv(...)` will:

1. Build the profile snapshot.
2. Render HTML using the existing 6 role templates.
3. Save the result in the `TalentCV` history table.
4. Return the CV id and HTML.

### 3. Endpoint updates (`app/talent/router.py`)

| Endpoint | Change |
|---|---|
| `POST /talent/{email}/cv/generate` | New optional `source` body field. Default: `"profile"`. `"hstaff"` keeps old behaviour. |
| `GET /talent/{email}/cv` | Uses profile rules. Returns 403 if profile is not published. |
| `GET /talent/{email}/cv/history` | No change. |
| `GET /talent/{email}/cv/{cv_id}` | No change. |
| `PATCH /talent/{email}/cv/prefs` | No change. |

### 4. Fix the "User not found" bug

Both `app/identity/router.py` and `app/profile/router.py` look up users by
email using unsafe `ilike` queries. We will replace them with the safer lookup
from `app/talent/access.py`:

1. Try exact case-insensitive email match.
2. If no `@` in the URL, try a prefix match with `%` and `_` escaped.
3. Any database error returns `None` instead of crashing.

### 5. Frontend updates

- `lib/api.ts`: `generateCv` sends `"source": "profile"` by default.
- `components/cv/cv-panel.tsx`: no major UI change; the hstaff sync button stays
  for languages/experience/certificates.

### 6. End-to-end leak test (`scripts/verify_cv_leaks.py`)

The old script writes directly to `Talent*` tables. The new script will use the
real API flow:

1. Admin creates a test user.
2. Test user creates profile skills and projects.
3. Test user submits them for review.
4. Admin/mentor approves them.
5. Admin publishes the profile and enables sections.
6. Test user generates a CV.
7. Assertions:
   - Verified public project is in the CV.
   - `public_summary` project shows only its summary.
   - `internal` / `client_confidential` projects are absent.
   - Draft / unverified items are absent.
   - Public CV respects `ProfileShare` settings.
   - CV history is stored.
   - Anonymous and non-owner users are denied.

## Success criteria

- [ ] CVs use only verified profile data.
- [ ] Confidential/internal/client-confidential content never appears in CVs or public views.
- [ ] Public CV respects `ProfileShare` publish/section settings.
- [ ] Generated CVs are stored in history.
- [ ] Section hiding and project reordering still work.
- [ ] `scripts/verify_cv_leaks.py` passes against a running backend.
- [ ] Frontend `npm run build` passes.
- [ ] The reported "User not found" profile lookup bug is fixed.

## Files we will edit

- `app/talent/services.py`
- `app/talent/router.py`
- `app/identity/router.py`
- `app/profile/router.py`
- `int-academy-platfrom-frontend/lib/api.ts`
- `int-academy-platfrom-frontend/components/cv/cv-panel.tsx`
- `scripts/verify_cv_leaks.py`

## Open questions

1. Should we keep the old hstaff-based CV (`source="hstaff"`) as a fallback?
   **Suggested answer:** Yes, for backward compatibility.
2. Should the public CV endpoint require the profile to be published?
   **Suggested answer:** Yes, return 403 if not published.
3. Should experience/certificates/languages also move to the profile workflow?
   **Suggested answer:** Not in this plan; keep using `Talent*` tables for now.

## Approval

After this plan is approved, implementation will start with the backend snapshot
builder and endpoint updates.
