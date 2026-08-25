#!/usr/bin/env python3
"""End-to-end leak check for the Profile-aware CV pipeline.

Runs against a live backend (default http://localhost:8000). It:
  1. logs in as the admin / superadmin,
  2. seeds a throwaway test user,
  3. creates local profile skills and projects through the real API,
  4. submits them for review and approves them,
  5. publishes the profile via ProfileShare,
  6. generates a CV from approved profile data,
  7. asserts that confidential / hidden / unverified data never leaks.

Exit code is non-zero on any leak / failure. Per-check PASS/FAIL is printed.

Usage:
  python scripts/verify_cv_leaks.py [--base URL] [--admin-email E] [--admin-password P]
                                    [--test-email E] [--test-password P]
Env vars: BASE_URL, ADMIN_EMAIL, ADMIN_PASSWORD, TEST_EMAIL, TEST_PASSWORD.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import requests

# --- restricted markers that must never leak ---
DRAFT_SKILL = "DraftInternalSkill"
DRAFT_PROJECT = "DraftUnverifiedProject"
INTERNAL_PROJECT = "Internal Academy Tool"
CLIENT_CONF_PROJECT = "ACME MegaCorp Secret Client Deal"
FULL_DETAIL_MARKER = "SecretImplementationDetail123"

_SECRETS = [
    DRAFT_SKILL,
    DRAFT_PROJECT,
    INTERNAL_PROJECT,
    CLIENT_CONF_PROJECT,
    FULL_DETAIL_MARKER,
]

# --- allowed markers that must be present when expected ---
ALLOWED_SKILL = "Python"
ALLOWED_PROJECT = "Open Source Dashboard"
PUBLIC_SUMMARY_PROJECT = "Public Summary Project"
PUBLIC_SUMMARY_TEXT = "A public-facing summary"

RESULT: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULT.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" ({detail})" if detail else ""))


def _env_flag(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _parse_args() -> dict:
    p = argparse.ArgumentParser(description="Profile-aware CV leak verification")
    p.add_argument("--base", default=_env_flag("BASE_URL", "http://localhost:8000"))
    p.add_argument(
        "--admin-email", default=_env_flag("ADMIN_EMAIL", "admin@academy.local")
    )
    p.add_argument(
        "--admin-password", default=_env_flag("ADMIN_PASSWORD", "admin12345")
    )
    p.add_argument(
        "--test-email", default=_env_flag("TEST_EMAIL", "cvleak@academy.local")
    )
    p.add_argument(
        "--test-password", default=_env_flag("TEST_PASSWORD", "cvleakpass12345")
    )
    return vars(p.parse_args())


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _login(base: str, email: str, password: str) -> str:
    r = requests.post(
        f"{base}/auth/login", json={"email": email, "password": password}, timeout=30
    )
    if r.status_code != 200:
        raise RuntimeError(f"login failed for {email}: {r.status_code} {r.text[:200]}")
    token = r.json().get("access_token")
    if not token:
        raise RuntimeError(f"no access_token for {email}")
    return token


def _admin_create_user(base: str, token: str, email: str, password: str) -> dict:
    r = requests.post(
        f"{base}/users/create",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "email": email,
            "full_name": "Profile CV Leak Test",
            "password": password,
            "roles": [],
        },
        timeout=30,
    )
    if r.status_code in (200, 201):
        return r.json()
    if r.status_code == 400 and "already exists" in r.text.lower():
        listing = requests.get(
            f"{base}/users", headers={"Authorization": f"Bearer {token}"}, timeout=30
        )
        listing.raise_for_status()
        for row in listing.json():
            if row.get("email") == email:
                return row
    raise RuntimeError(f"admin create user failed: {r.status_code} {r.text[:300]}")


def _post(
    base: str, token: str, path: str, body: dict | None = None
) -> requests.Response:
    return requests.post(
        f"{base}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=body or {},
        timeout=30,
    )


def _create_or_reuse(
    base: str, token: str, path: str, list_path: str, body: dict
) -> dict:
    """Create a profile item, reusing an existing one with the same name on
    re-runs (the profile API rejects duplicate names)."""
    r = _post(base, token, path, body)
    if r.status_code in (200, 201):
        return r.json()
    if r.status_code == 400 and "already" in r.text.lower():
        key = "name" if body.get("name") is not None else "title"
        want = body[key]
        listing = _get(base, token, list_path)
        if listing.status_code in (200, 201):
            for row in listing.json():
                if row.get(key) == want:
                    return row
    raise RuntimeError(f"create {path} failed: {r.status_code} {r.text[:300]}")


def _get(base: str, token: str, path: str) -> requests.Response:
    return requests.get(
        f"{base}{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )


def _patch(base: str, token: str, path: str, body: dict) -> requests.Response:
    return requests.patch(
        f"{base}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=30,
    )


def _put(base: str, token: str, path: str, body: dict) -> requests.Response:
    return requests.put(
        f"{base}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Main test flow
# ---------------------------------------------------------------------------


def main() -> int:
    args = _parse_args()
    base = args["base"].rstrip("/")

    try:
        admin_token = _login(base, args["admin_email"], args["admin_password"])
    except RuntimeError as e:
        check("setup: admin login", False, str(e))
        _report_and_exit(1)

    admin_created = _admin_create_user(
        base, admin_token, args["test_email"], args["test_password"]
    )
    email = args["test_email"]

    try:
        test_token = _login(base, email, args["test_password"])
    except RuntimeError as e:
        check("setup: test user login", False, str(e))
        _report_and_exit(1)

    # 1. Create allowed profile items.
    slug = email.split("@")[0]
    skill = _create_or_reuse(
        base,
        test_token,
        "/profile/me/skills",
        f"/profile/{slug}/skills",
        {
            "name": ALLOWED_SKILL,
            "category": "programming_languages",
            "level": "advanced",
        },
    )
    allowed_project = _create_or_reuse(
        base,
        test_token,
        "/profile/me/projects",
        f"/profile/{slug}/projects",
        {
            "title": ALLOWED_PROJECT,
            "description": "A public dashboard built with Python",
            "technologies": ["Python", "FastAPI"],
            "confidentiality": "public",
            "kind": "external",
        },
    )
    public_summary_project = _create_or_reuse(
        base,
        test_token,
        "/profile/me/projects",
        f"/profile/{slug}/projects",
        {
            "title": PUBLIC_SUMMARY_PROJECT,
            "description": f"Full detail: {FULL_DETAIL_MARKER}",
            "public_summary": PUBLIC_SUMMARY_TEXT,
            "technologies": ["React"],
            "confidentiality": "public_summary",
            "kind": "external",
        },
    )
    internal_project = _create_or_reuse(
        base,
        test_token,
        "/profile/me/projects",
        f"/profile/{slug}/projects",
        {
            "title": INTERNAL_PROJECT,
            "description": "Internal tooling only",
            "technologies": ["Python"],
            "confidentiality": "internal",
            "kind": "external",
        },
    )
    client_conf_project = _create_or_reuse(
        base,
        test_token,
        "/profile/me/projects",
        f"/profile/{slug}/projects",
        {
            "title": CLIENT_CONF_PROJECT,
            "description": "Secret client work",
            "technologies": ["Python"],
            "confidentiality": "client_confidential",
            "kind": "external",
        },
    )
    draft_skill = _create_or_reuse(
        base,
        test_token,
        "/profile/me/skills",
        f"/profile/{slug}/skills",
        {"name": DRAFT_SKILL, "category": "programming_languages", "level": "beginner"},
    )
    draft_project = _create_or_reuse(
        base,
        test_token,
        "/profile/me/projects",
        f"/profile/{slug}/projects",
        {
            "title": DRAFT_PROJECT,
            "description": "Not finished",
            "technologies": ["Python"],
            "confidentiality": "public",
            "kind": "external",
        },
    )

    # 2. Submit allowed items for review (drafts stay draft; already-submitted or
    #    verified items from a previous run are skipped).
    for item_type, item in (
        ("skill", skill),
        ("project", allowed_project),
        ("project", public_summary_project),
        ("project", internal_project),
        ("project", client_conf_project),
    ):
        if item.get("review_state") not in ("draft", "rejected"):
            continue
        r = _post(base, test_token, f"/profile/me/{item_type}/{item['id']}/submit", {})
        if r.status_code not in (200, 201):
            check(f"submit {item_type} {item['title']}", False, r.text[:200])
            _report_and_exit(1)

    # 3. Approve all submitted items as admin/mentor (verified items skipped).
    for item_type, item in (
        ("skill", skill),
        ("project", allowed_project),
        ("project", public_summary_project),
        ("project", internal_project),
        ("project", client_conf_project),
    ):
        if item.get("review_state") == "verified":
            continue
        r = _post(
            base,
            admin_token,
            f"/profile/reviews/{item_type}/{item['id']}/approve",
            {"verification_level": "confirmed"},
        )
        if r.status_code not in (200, 201):
            check(f"approve {item_type} {item['title']}", False, r.text[:200])
            _report_and_exit(1)

    # 4. Approve the public summary text separately.
    r = _post(
        base,
        admin_token,
        f"/profile/projects/{public_summary_project['id']}/approve-summary",
        {},
    )
    if r.status_code not in (200, 201):
        check("approve public summary", False, r.text[:200])
        _report_and_exit(1)

    # 5. Publish the profile and enable skills + projects sections.
    r = _put(
        base,
        admin_token,
        f"/profile/{email}/share",
        {
            "is_published": True,
            "sections": {
                "bio": True,
                "skills": True,
                "projects": True,
                "languages": True,
                "certifications": True,
                "external_accounts": True,
            },
        },
    )
    if r.status_code not in (200, 201):
        check("publish profile", False, r.text[:200])
        _report_and_exit(1)

    # 6. Generate a CV from approved profile data.
    gen = _post(
        base,
        test_token,
        f"/talent/{email}/cv/generate",
        {"target_role": "backend-developer", "source": "profile"},
    )
    check(
        "generate CV from profile source",
        gen.status_code == 200 and bool(gen.json().get("cv_id")),
        f"status={gen.status_code}",
    )
    if gen.status_code != 200:
        _report_and_exit(1)

    cv_id = gen.json()["cv_id"]
    html = gen.json().get("html", "")
    snapshot = gen.json().get("snapshot", {})
    combined = html + json.dumps(snapshot, default=str)

    leaked = [s for s in _SECRETS if s in combined]
    check(
        "no restricted data in generated CV html+snapshot",
        not leaked,
        ", ".join(leaked) if leaked else "",
    )

    check(
        "allowed skill appears in CV",
        ALLOWED_SKILL in combined,
    )
    check(
        "allowed project appears in CV",
        ALLOWED_PROJECT in combined,
    )
    check(
        "public_summary project appears with summary only",
        PUBLIC_SUMMARY_PROJECT in combined and PUBLIC_SUMMARY_TEXT in combined,
    )
    check(
        "full detail marker absent from public_summary project",
        FULL_DETAIL_MARKER not in combined,
    )
    check(
        "internal project absent from CV",
        INTERNAL_PROJECT not in combined,
    )
    check(
        "client_confidential project absent from CV",
        CLIENT_CONF_PROJECT not in combined,
    )
    check(
        "draft skill absent from CV",
        DRAFT_SKILL not in combined,
    )
    check(
        "draft project absent from CV",
        DRAFT_PROJECT not in combined,
    )

    # 7. Public CV endpoint also leaks nothing.
    pub = _get(base, test_token, f"/talent/{email}/cv")
    if pub.status_code == 200:
        pub_text = pub.text
        leaked_pub = [s for s in _SECRETS if s in pub_text]
        check(
            "public CV endpoint leaks no restricted data",
            not leaked_pub,
            ", ".join(leaked_pub) if leaked_pub else "",
        )
        check(
            "public CV shows allowed content",
            ALLOWED_SKILL in pub_text and ALLOWED_PROJECT in pub_text,
        )
    else:
        check("public CV endpoint", False, f"status={pub.status_code}")

    # 7b. Unpublishing the profile must make the public CV return 403 — the
    # guard is the ProfileShare switch, not the snapshot contents (a user with
    # an hstaff bio must not leak it while unpublished).
    _put(
        base,
        admin_token,
        f"/profile/{email}/share",
        {"is_published": False},
    )
    unpublished = _get(base, test_token, f"/talent/{email}/cv")
    check(
        "unpublished profile public CV returns 403",
        unpublished.status_code == 403,
        f"status={unpublished.status_code}",
    )
    _put(
        base,
        admin_token,
        f"/profile/{email}/share",
        {"is_published": True},
    )
    republished = _get(base, test_token, f"/talent/{email}/cv")
    check(
        "republished profile public CV works again",
        republished.status_code == 200,
        f"status={republished.status_code}",
    )

    # 8. ProfileShare section curation affects public CV.
    _put(
        base,
        admin_token,
        f"/profile/{email}/share",
        {"sections": {"projects": False}},
    )
    pub_no_projects = _get(base, test_token, f"/talent/{email}/cv")
    if pub_no_projects.status_code == 200:
        check(
            "public CV hides projects when section disabled",
            ALLOWED_PROJECT not in pub_no_projects.text,
        )
    else:
        check("public CV hide projects", False, f"status={pub_no_projects.status_code}")
    # Re-enable for subsequent checks.
    _put(
        base,
        admin_token,
        f"/profile/{email}/share",
        {"sections": {"projects": True}},
    )

    # 9. Owner can save CV prefs; non-owner cannot.
    prefs_body = {
        "target_role": "backend-developer",
        "hidden_sections": ["skills"],
        "project_order": [
            str(public_summary_project["id"]),
            str(allowed_project["id"]),
        ],
    }
    owner_prefs = _patch(base, test_token, f"/talent/{email}/cv/prefs", prefs_body)
    check(
        "owner can save CV prefs",
        owner_prefs.status_code == 200,
        f"status={owner_prefs.status_code}",
    )

    owner_read_prefs = _get(
        base, test_token, f"/talent/{email}/cv/prefs?target_role=backend-developer"
    )
    check(
        "owner can read stored CV prefs",
        owner_read_prefs.status_code == 200
        and owner_read_prefs.json().get("hidden_sections") == ["skills"],
        f"status={owner_read_prefs.status_code}",
    )

    # Authenticated non-owner (a plain user) must be denied prefs + history.
    other_email = "other-cvleak@academy.local"
    _admin_create_user(base, admin_token, other_email, "otherleakpass12345")
    other_token = _login(base, other_email, "otherleakpass12345")
    non_owner_prefs = _patch(base, other_token, f"/talent/{email}/cv/prefs", prefs_body)
    check(
        "non-owner cannot save CV prefs",
        non_owner_prefs.status_code in (403, 404),
        f"status={non_owner_prefs.status_code}",
    )

    non_owner_history = _get(base, other_token, f"/talent/{email}/cv/history")
    check(
        "non-owner cannot view CV history",
        non_owner_history.status_code in (403, 404),
        f"status={non_owner_history.status_code}",
    )

    anon_history = requests.get(f"{base}/talent/{email}/cv/history", timeout=15)
    check(
        "anonymous history request denied",
        anon_history.status_code in (401, 403),
        f"status={anon_history.status_code}",
    )

    # 10. Stored CV snapshot and html hold no restricted data.
    stored = _get(base, test_token, f"/talent/{email}/cv/{cv_id}")
    if stored.status_code == 200:
        stored_html = stored.json().get("html", "")
        stored_snap = stored.json().get("snapshot") or {}
        leaked_stored = [
            s
            for s in _SECRETS
            if s in stored_html or s in json.dumps(stored_snap, default=str)
        ]
        check(
            "stored CV html+snapshot holds no restricted data",
            not leaked_stored,
            ", ".join(leaked_stored) if leaked_stored else "",
        )
    else:
        check("stored CV fetch", False, f"status={stored.status_code}")

    _report_and_exit()


def _report_and_exit(code: int | None = None) -> None:
    passed = sum(1 for _, ok, _ in RESULT if ok)
    total = len(RESULT)
    print(f"\n{passed}/{total} checks passed.")
    if code is not None:
        sys.exit(code)
    sys.exit(0 if all(ok for _, ok, _ in RESULT) else 1)


if __name__ == "__main__":
    main()
