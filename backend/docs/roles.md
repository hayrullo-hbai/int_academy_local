# Roles & Permissions

The authoritative catalog lives in
[`app/identity/rbac_catalog.py`](../app/identity/rbac_catalog.py) and is seeded
into the database on every startup. The role *predicates* — the code that
actually decides access — live in three small modules:

- [`app/identity/services.py`](../app/identity/services.py) — management, user
  administration, elevation rules
- [`app/onboarding/access.py`](../app/onboarding/access.py) — onboarding board
- [`app/academy/access.py`](../app/academy/access.py) — academy content

All three are mirrored in the frontend at `frontend/lib/roles.ts`. **Changing a
rule means changing both sides.**

---

## 1. The catalog

Level 0 is highest. Roles marked *system* came from hstaff and cannot be deleted
through the API.

| Level | Role | System | Purpose |
|---|---|---|---|
| 0 | `superadmin` | ✅ | Full access; bypasses every permission check via `is_superuser` |
| 1 | `c-level` | ✅ | The single C-suite role — CEO, CTO, COO, CFO, CMO and all chief officers |
| 2 | `advisor` | ✅ | High-level advisory; read-only over talents |
| 3 | `academy-manager` | ❌ | Owns onboarding end-to-end and administers academy content |
| 3 | `examiner` | ❌ | Local role: authors and grades academy content without being a learner |
| 4 | `department-head` | ✅ | Department management and mentorship |
| 5 | `hr` | ✅ | People operations; runs the intro call and culture-fit interviews |
| 6 | `pm` | ✅ | Project management |
| 7 | `mentor` | ✅ | Mentors talents |
| 8 | `talent` | ✅ | Regular employee — highest learner tier |
| 9 | `foundation` | ✅ | Foundation program |
| 10 | `softlanding` | ✅ | Soft-landing program |
| 11 | `school` | ✅ | Entry tier, granted when local onboarding completes |

### The talent journey

```
  onboarding complete
          │
          ▼
       school ──▶ softlanding ──▶ foundation ──▶ talent
       └──────────── "learner" (LEARNER_ROLES) ────────────┘
```

"Learner" is not a role. It is the *set* of those four tiers. Anyone holding one
of them gets the academy experience. Promotion between tiers happens in hstaff,
not here.

---

## 2. Role groupings used by the code

These sets are what the access predicates actually test.

| Group | Members | Defined in | Governs |
|---|---|---|---|
| `LEARNER_ROLES` | school, softlanding, foundation, talent | `identity/enums.py` | who is a learner |
| `ELEVATED_ROLES` | superadmin, c-level, advisor, cfo | `identity/services.py` | who may assign elevated roles and edit elevated users |
| `EXECUTIVE_ROLES` | c-level | `identity/services.py` | who may be granted the C-suite role |
| `MANAGEMENT_ROLES` | academy-manager, hr, department-head, c-level, superadmin | `identity/services.py` | role CRUD |
| `USER_ADMIN_ROLES` | superadmin, academy-manager | `identity/services.py` | create/edit/delete users |
| `STAFF_ROLES` | academy-manager, hr, superadmin | `onboarding/access.py` | see the onboarding board |
| `ASSIGNER_ROLES` | academy-manager, superadmin | `onboarding/access.py` | assign interviewers |
| `ADMIN_ROLES` | academy-manager, superadmin | `academy/access.py` | academy administration |
| `EXAMINER_ROLES` | examiner | `academy/access.py` | academy authoring |
| `CONTENT_ADMIN_ROLES` | the two above combined | `academy/access.py` | who may author content |
| `ACCESS_ROLES` | learners + examiner + academy admins | `academy/access.py` | who may open the academy |

Note the same word means different things in different modules:
`onboarding.access.MANAGEMENT_ROLES` is narrower (three roles) than
`identity.services.MANAGEMENT_ROLES` (five). Read the import, not the name.

---

## 3. Capability matrix

✅ full · ⚙️ conditional (see note) · ❌ denied

| Capability | superadmin | c-level | advisor | academy-manager | examiner | hr | dept-head / pm / mentor | learners |
|---|---|---|---|---|---|---|---|---|
| **Auth & self-service** |
| Log in, manage own profile | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Switch active role | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Directory** |
| View user list | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| View public profiles | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **User administration** |
| Create users | ✅ | ❌ | ❌ | ⚙️ ¹ | ❌ | ❌ | ❌ | ❌ |
| Set user roles | ✅ | ❌ | ❌ | ⚙️ ² | ❌ | ❌ | ❌ | ❌ |
| Activate / deactivate | ✅ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Delete users | ✅ | ❌ | ❌ | ⚙️ ² | ❌ | ❌ | ❌ | ❌ |
| Grant `c-level` | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Grant `superadmin` | ❌ ³ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Roles** |
| Create / edit roles | ✅ | ✅ | ❌ | ✅ | ❌ | ✅ | ⚙️ ⁴ | ❌ |
| Delete a local role | ✅ | ✅ | ❌ | ✅ | ❌ | ✅ | ⚙️ ⁴ | ❌ |
| Delete a system role | ❌ ³ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Onboarding** |
| See the board | ✅ | ❌ | ❌ | ✅ | ❌ | ✅ | ❌ | ❌ |
| Link / sync the sheet | ✅ | ❌ | ❌ | ✅ | ❌ | ✅ | ❌ | ❌ |
| Edit board rows | ✅ | ❌ | ❌ | ✅ | ❌ | ✅ | ❌ | ❌ |
| Assign interviewers | ✅ | ❌ | ❌ | ✅ | ❌ | ❌ ⁵ | ❌ | ❌ |
| Conduct intro call / culture fit | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ |
| Conduct tech interview | ✅ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Submit a stage report | ✅ | ❌ | ❌ | ⚙️ ⁶ | ❌ | ⚙️ ⁶ | ❌ | ❌ |
| Decide payment | ✅ | ❌ | ❌ | ✅ | ❌ | ✅ | ❌ | ❌ |
| Complete access (grants `school`) | ✅ | ❌ | ❌ | ✅ | ❌ | ✅ | ❌ | ❌ |
| Verify an address | ✅ | ❌ | ❌ | ✅ | ❌ | ✅ | ❌ | ❌ |
| Read the discussion chat | ✅ | ❌ | ❌ | ⚙️ ⁷ | ❌ | ⚙️ ⁷ | ❌ | ❌ |
| **Academy** |
| Open the academy | ✅ | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ | ✅ |
| Solve problems, submit | ✅ | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ | ✅ |
| Use Data Lab | ✅ | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ | ✅ |
| Keep private notebooks | ✅ | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ | ✅ |
| Author problems / data problems | ✅ | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ |
| Upload datasets | ✅ | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ |
| Create exams, invite to private exams | ✅ | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ |
| Edit / delete own content | ✅ | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ | ⚙️ ⁸ |
| See reference solutions & hidden tests | ✅ | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ | ⚙️ ⁸ |
| View everyone's exam submissions | ✅ | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ |
| View any user's academy progress | ✅ | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ | ⚙️ ⁹ |
| **hstaff** |
| Create / bulk-sync hstaff users | ✅ | ⚙️ ¹⁰ | ⚙️ ¹⁰ | ✅ | ⚙️ ¹⁰ | ✅ | ⚙️ ¹⁰ | ⚙️ ¹⁰ |
| Use the passthrough proxy | ⚙️ ¹¹ | ⚙️ ¹¹ | ⚙️ ¹¹ | ⚙️ ¹¹ | ⚙️ ¹¹ | ⚙️ ¹¹ | ⚙️ ¹¹ | ⚙️ ¹¹ |

**Notes**

1. `is_user_admin` allows academy-manager, but `POST /users/create` then rejects a
   non-elevated academy-manager explicitly. In practice only a superadmin creates
   local users.
2. Allowed, but never against an elevated target — a non-elevated user cannot
   touch a superadmin, c-level, advisor or cfo account at all.
3. Blocked for everyone through the API. A superadmin is created only by the
   startup seed; system roles are managed only by editing `rbac_catalog.py`.
4. Only `department-head` qualifies as management here; `pm` and `mentor` do not.
5. Deliberate: HR runs interviews but does not decide who runs them.
6. Only the interviewer actually **assigned** to that stage may report on it.
7. Only discussion participants — the assigned interviewers plus management.
8. A learner has no authoring rights, but does own their notebooks and
   submissions and may edit/delete those. Reference solutions are visible only
   for content they own.
9. Their own always; anyone else's only if that person made it public.
10. Allowed for anyone holding the `users:create` permission, in addition to the
    named roles.
11. Any user whose account is **hstaff-linked** (`source="hstaff"`), regardless of
    role. hstaff's own RBAC then gates the forwarded request. Local-only accounts
    get a 400.

---

## 4. Permission codenames

The `permissions` table mirrors hstaff's catalog (`users:read`, `day_off:approve`,
`rise:reports_read`, and ~65 more). It exists so that the two systems agree on
vocabulary and so hstaff-sourced permissions can be cached and displayed.

**Almost nothing in this backend actually gates on a codename.** The exceptions
are the two hstaff user-management endpoints, which accept `users:create`. Every
other check is role-based. Do not assume adding a permission to a role grants
access to anything here — grant the role, or add the check.

For hstaff-sourced users, `permission_codenames` returns the list cached from
hstaff's `/permissions/me` at login. For local users it is computed from their
roles.

---

## 5. Adding a role

1. Add it to `ROLES` in `rbac_catalog.py` with `is_system: False`.
2. Add it to whichever grouping sets in `access.py` / `services.py` should
   include it.
3. Mirror both in `frontend/lib/roles.ts`.
4. Restart — `seed_rbac()` upserts it.

Do not create roles directly in the database. Anything absent from the catalog
and marked `is_system=True` is **deleted** on the next boot; only
`is_system=False` roles created through `POST /roles` survive.
