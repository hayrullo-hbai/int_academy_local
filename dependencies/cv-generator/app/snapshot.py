"""CV snapshot builder.

Constructs the canonical, allow-listed data dict that ``templates.render``
turns into HTML. This is a stateless adaptation of the logic that previously
lived in ``int-academy-platform-backend/app/talent/services.py``; it works with
the JSON payloads sent by the backend instead of querying the database.
"""

from typing import Any

# ---------------------------------------------------------------------------
# Replicated enums / constants (do NOT import from the backend).
# ---------------------------------------------------------------------------


class ReviewState:
    VERIFIED = "verified"


class ProjectConfidentiality:
    PUBLIC = "public"
    PUBLIC_SUMMARY = "public_summary"
    ACADEMY_ONLY = "academy_only"
    INTERNAL = "internal"
    CLIENT_CONFIDENTIAL = "client_confidential"


class SocialPlatform:
    GITHUB = "github"
    LINKEDIN = "linkedin"


class ProfileSection:
    BIO = "bio"
    SKILLS = "skills"
    PROJECTS = "projects"
    LANGUAGES = "languages"
    CERTIFICATIONS = "certifications"
    EXTERNAL_ACCOUNTS = "external_accounts"


# Map a CV section key to the corresponding ProfileShare section key.
_PROFILE_SECTION_MAPPING = {
    "summary": "bio",
    "contact": "external_accounts",
    "skills": "skills",
    "projects": "projects",
    "languages": "languages",
    "certificates": "certifications",
}

_PUBLICLY_RENDERABLE_CONFIDENTIALITY = {
    ProjectConfidentiality.PUBLIC,
    ProjectConfidentiality.PUBLIC_SUMMARY,
}


# ---------------------------------------------------------------------------
# Small defensive helpers for hstaff-profile dicts.
# ---------------------------------------------------------------------------


def _first_list(data: dict, *keys):
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return value
    return None


def _first_str(data: dict, *keys, default=""):
    for key in keys:
        value = data.get(key)
        if value is not None:
            return str(value).strip()
    return default


def _as_dict(item, name_key: str = "name") -> dict:
    if isinstance(item, dict):
        return item
    if isinstance(item, str) and item.strip():
        return {name_key: item.strip()}
    return {}


def _is_verified(item: dict) -> bool:
    if item.get("verified") is True or item.get("is_verified") is True:
        return True
    return (item.get("status") or "").lower() == "verified"


def _item_urls(item: dict) -> list:
    raw = item.get("urls") or item.get("links")
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    extras = [
        str(item[k]).strip()
        for k in ("url", "site", "site_url", "repo", "repo_url", "project_url")
        if item.get(k)
    ]
    return [u for u in extras if u]


def _item_bullets(item: dict) -> list:
    raw = item.get("bullets")
    if isinstance(raw, list):
        return [str(b).strip() for b in raw if str(b).strip()]
    desc = item.get("description") or item.get("summary")
    if isinstance(desc, str) and desc.strip():
        return [desc.strip()]
    return []


def _item_skills(item: dict) -> list:
    raw = item.get("skills") or item.get("technologies") or item.get("tags")
    if isinstance(raw, list):
        return [str(s).strip() for s in raw if str(s).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


def _item_id(item: dict) -> str:
    value = item.get("id") or item.get("source_id")
    if value is None:
        return ""
    return str(value).strip()


# ---------------------------------------------------------------------------
# Snapshot builders
# ---------------------------------------------------------------------------


def _role_label(target_role: str) -> str:
    return " ".join(p.capitalize() for p in (target_role or "").split("-"))


def _profile_section_mapping(cv_section: str) -> str:
    return _PROFILE_SECTION_MAPPING.get(cv_section, cv_section)


def _public_deny(h: dict, public: bool) -> set:
    """Section keys to drop from a public snapshot."""
    if not public:
        return set()
    deny: set = set()
    if _first_list(h, "experience", "work_experience", "employment") is None:
        deny.add("experience")
    if _first_list(h, "certificates", "certifications", "courses") is None:
        deny.add("certificates")
    return deny


def _build_contact(user: dict, h: dict) -> dict:
    contact: dict = {}
    email = (user.get("email") or "").strip()
    if email:
        contact["email"] = email
    phone = (user.get("phone") or "").strip() or _first_str(h, "phone", default="")
    if phone:
        contact["phone"] = phone
    github = _first_str(
        h, "github", "github_url", "github_username", "github_handle", default=""
    )
    if github:
        contact["github"] = github
    linkedin = _first_str(
        h, "linkedin", "linkedin_url", "linkedin_username", default=""
    )
    if linkedin:
        contact["linkedin"] = linkedin
    website = _first_str(h, "website", "website_url", "blog", "web", default="")
    if website:
        contact["website"] = website
    return contact


def _academy_block(user: dict, h: dict) -> dict | None:
    """Optional academy progress/scores — only surfaced when explicitly public."""
    if not user.get("academy_progress_public"):
        return None
    block: dict = {}
    for key in (
        "academy_scores",
        "solved_problems",
        "data_problems_solved",
        "exams_passed",
        "progress",
    ):
        value = h.get(key)
        if value is not None:
            block[key] = value
    return block or None


def _item_content(item: dict) -> dict:
    """Return the approved snapshot if present, otherwise the item itself."""
    return item.get("approved_snapshot") or item


def _apply_prefs(snapshot: dict, prefs: dict | None, public: bool) -> None:
    """Apply hidden-section and project-order prefs (owner/manager path only)."""
    if public or not prefs:
        return
    hidden = list(prefs.get("hidden_sections") or [])
    for section in hidden:
        snapshot.pop(section, None)
    order = list(prefs.get("project_order") or [])
    if order and "projects" in snapshot:
        by_id = {p["id"]: p for p in snapshot["projects"]}
        snapshot["projects"] = [
            by_id[pid] for pid in order if pid in by_id
        ] + [p for p in snapshot["projects"] if p["id"] not in set(order)]


def _share_shows(share: dict, section: str) -> bool:
    """Mirror of ProfileShare.shows(section)."""
    if share is None:
        return False
    sections = share.get("sections") or {}
    return bool(sections.get(_profile_section_mapping(section), False))


def allowlist_snapshot(
    user: dict,
    talent: dict,
    target_role: str,
    prefs: dict | None = None,
    public: bool = False,
) -> dict:
    """Build the canonical CV data dict from hstaff-mirror talent data."""
    h = user.get("hstaff_profile") or talent.get("hstaff_profile") or {}
    snapshot: dict = {
        "target_role": target_role,
        "target_role_label": _role_label(target_role),
        "personal": {"full_name": user.get("full_name") or ""},
        "summary": {"bio": _first_str(h, "bio", "summary", "about", default="")},
        "contact": _build_contact(user, h),
    }

    public_deny = _public_deny(h, public)

    # skills: verified only
    snapshot["skills"] = [
        {
            "name": s.get("name", ""),
            "level": s.get("level", ""),
            "category": s.get("category", ""),
        }
        for s in talent.get("skills") or []
        if s.get("verified")
    ]

    # projects: verified + not hidden + not confidential
    snapshot["projects"] = []
    for p in talent.get("projects") or []:
        if not p.get("verified") or p.get("hidden") or p.get("confidential"):
            continue
        snapshot["projects"].append(
            {
                "id": str(p.get("id", "")),
                "title": p.get("title", ""),
                "urls": list(p.get("urls") or []),
                "start_date": p.get("start_date", ""),
                "end_date": p.get("end_date", ""),
                "present": bool(p.get("present")),
                "bullets": list(p.get("bullets") or []),
                "skills": list(p.get("skills") or []),
            }
        )

    # languages: no verification flags — all shown
    snapshot["languages"] = [
        {"name": lang.get("name", ""), "level": lang.get("level", "")}
        for lang in talent.get("languages") or []
    ]

    # experience
    if "experience" in public_deny:
        snapshot["experience"] = []
    else:
        snapshot["experience"] = [
            {
                "id": str(e.get("id", "")),
                "company": e.get("company", ""),
                "role": e.get("role", ""),
                "start_date": e.get("start_date", ""),
                "end_date": e.get("end_date", ""),
                "present": bool(e.get("present")),
                "description": e.get("description", ""),
                "technologies": list(e.get("technologies") or []),
                "company_logo_url": e.get("company_logo_url", ""),
            }
            for e in talent.get("experience") or []
            if e.get("verified") and not e.get("hidden")
        ]

    # certificates
    if "certificates" in public_deny:
        snapshot["certificates"] = []
    else:
        snapshot["certificates"] = [
            {
                "title": c.get("title", ""),
                "issuer": c.get("issuer", ""),
                "issue_date": c.get("issue_date", ""),
            }
            for c in talent.get("certificates") or []
            if c.get("verified") and not c.get("hidden")
        ]

    academy = _academy_block(user, h)
    if academy:
        snapshot["academy"] = academy

    _apply_prefs(snapshot, prefs, public)
    return snapshot


def _build_profile_contact(
    user: dict, h: dict, profile: dict, public: bool
) -> dict:
    """Contact block sourced from the user row + verified profile social accounts."""
    contact: dict = {}
    email = (user.get("email") or "").strip()
    if email:
        contact["email"] = email
    phone = (user.get("phone") or "").strip() or _first_str(h, "phone", default="")
    if phone:
        contact["phone"] = phone

    github = _first_str(
        h, "github", "github_url", "github_username", "github_handle", default=""
    )
    linkedin = _first_str(
        h, "linkedin", "linkedin_url", "linkedin_username", default=""
    )
    website = _first_str(h, "website", "website_url", "blog", "web", default="")

    for account in profile.get("social_accounts") or []:
        if account.get("review_state") != ReviewState.VERIFIED:
            continue
        content = _item_content(account)
        platform = content.get("platform") or account.get("platform", "")
        url = content.get("url") or account.get("url", "")
        username = content.get("username") or account.get("username", "")
        if platform == SocialPlatform.GITHUB and (url or username):
            github = url or f"https://github.com/{username}"
        elif platform == SocialPlatform.LINKEDIN and (url or username):
            linkedin = url or f"https://www.linkedin.com/in/{username}"

    if github:
        contact["github"] = github
    if linkedin:
        contact["linkedin"] = linkedin
    if website:
        contact["website"] = website
    return contact


def build_profile_cv_snapshot(
    user: dict,
    profile: dict,
    talent: dict,
    target_role: str,
    prefs: dict | None = None,
    public: bool = False,
) -> dict:
    """Build a CV snapshot from local profile-workflow data.

    Only verified profile items are included. Project confidentiality is
    enforced: internal and client_confidential projects are dropped,
    public_summary projects render only their approved summary.
    """
    h = profile.get("hstaff_profile") or user.get("hstaff_profile") or {}
    snapshot: dict = {
        "target_role": target_role,
        "target_role_label": _role_label(target_role),
        "personal": {"full_name": user.get("full_name") or ""},
        "summary": {"bio": _first_str(h, "bio", "summary", "about", default="")},
        "contact": _build_profile_contact(user, h, profile, public),
    }

    share = profile.get("share")

    def _section_visible(section: str) -> bool:
        if not public:
            return True
        return _share_shows(share, section)

    # Skills: verified only, from approved_snapshot.
    if _section_visible("skills"):
        skills = []
        for skill in profile.get("skills") or []:
            if skill.get("review_state") != ReviewState.VERIFIED:
                continue
            content = _item_content(skill)
            skills.append(
                {
                    "name": content.get("name") or skill.get("name", ""),
                    "category": content.get("category") or skill.get("category", ""),
                    "level": content.get("level") or skill.get("level", ""),
                }
            )
        snapshot["skills"] = skills

    # Projects: verified + confidentiality-aware.
    if _section_visible("projects"):
        projects = []
        for project in profile.get("projects") or []:
            if project.get("review_state") != ReviewState.VERIFIED:
                continue
            content = _item_content(project)
            confidentiality = content.get("confidentiality") or project.get(
                "confidentiality", ""
            )
            if confidentiality in (
                ProjectConfidentiality.INTERNAL,
                ProjectConfidentiality.CLIENT_CONFIDENTIAL,
            ):
                continue

            item: dict = {
                "id": str(content.get("id") or project.get("id", "")),
                "title": content.get("title") or project.get("title", ""),
                "role": content.get("role") or project.get("role", ""),
                "start_date": content.get("start_date")
                or project.get("start_date", ""),
                "end_date": content.get("end_date") or project.get("end_date", ""),
                "present": bool(
                    content.get("present")
                    if content.get("present") is not None
                    else project.get("present", False)
                ),
                "urls": [],
                "bullets": [],
                "skills": [],
            }

            if confidentiality == ProjectConfidentiality.PUBLIC_SUMMARY:
                summary = ""
                if project.get("public_summary_approved"):
                    summary = content.get("public_summary") or project.get(
                        "public_summary", ""
                    )
                item["description"] = summary
                if summary:
                    item["bullets"] = [summary]
            else:
                item["description"] = content.get("description") or project.get(
                    "description", ""
                )
                description = item["description"]
                if description:
                    item["bullets"] = [description]
                repo = content.get("repository_url") or project.get("repository_url", "")
                demo = content.get("live_demo_url") or project.get("live_demo_url", "")
                if repo:
                    item["urls"].append(repo)
                if demo:
                    item["urls"].append(demo)
                item["skills"] = list(
                    content.get("technologies")
                    or project.get("technologies")
                    or []
                )
            projects.append(item)
        snapshot["projects"] = projects

    # Languages / Experience / Certificates: continue using talent data.
    if _section_visible("languages"):
        snapshot["languages"] = [
            {"name": lang.get("name", ""), "level": lang.get("level", "")}
            for lang in talent.get("languages") or []
        ]

    if _section_visible("experience"):
        snapshot["experience"] = [
            {
                "id": str(e.get("id", "")),
                "company": e.get("company", ""),
                "role": e.get("role", ""),
                "start_date": e.get("start_date", ""),
                "end_date": e.get("end_date", ""),
                "present": bool(e.get("present")),
                "description": e.get("description", ""),
                "technologies": list(e.get("technologies") or []),
                "company_logo_url": e.get("company_logo_url", ""),
            }
            for e in talent.get("experience") or []
            if e.get("verified") and not e.get("hidden")
        ]

    if _section_visible("certificates"):
        snapshot["certificates"] = [
            {
                "title": c.get("title", ""),
                "issuer": c.get("issuer", ""),
                "issue_date": c.get("issue_date", ""),
            }
            for c in talent.get("certificates") or []
            if c.get("verified") and not c.get("hidden")
        ]

    academy = _academy_block(user, h)
    if academy:
        snapshot["academy"] = academy

    _apply_prefs(snapshot, prefs, public)
    return snapshot
