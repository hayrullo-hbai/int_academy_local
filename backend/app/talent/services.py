"""Talent profile CV services.

Responsibilities:
* ``sync_from_hstaff`` — best-effort pull of an hstaff talent profile into the
  local ``talent_*`` tables (never raises on hstaff failure; keeps last-known rows).
* ``allowlist_snapshot`` — build the canonical, **allow-listed** data dict a CV is
  rendered from. Restricted data is excluded here, so no downstream step can leak
  it (no-default-trust).
* ``generate_cv`` / ``latest_cv`` / ``get_cv`` / ``save_prefs`` —
  generation + layout prefs.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.hstaff import client as hstaff
from app.talent import cv_client
from app.talent.access import CV_SECTIONS
from app.talent.models import (
    TalentCertificate,
    TalentCVPrefs,
    TalentCV,
    TalentExperience,
    TalentLanguage,
    TalentProject,
    TalentSkill,
)

# ---------------------------------------------------------------------------
# Small defensive hstaff-schema helpers (hstaff layouts vary; never trust keys)
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
    """The upstream (hstaff) id of a section item, if the source carries one."""
    value = item.get("id") or item.get("source_id")
    if value is None:
        return ""
    return str(value).strip()


# ---------------------------------------------------------------------------
# Sync from hstaff (best-effort upsert; never raises on hstaff failure)
# ---------------------------------------------------------------------------


def _prune_removed(db: Session, model, target, matched_source_ids: set) -> None:
    """Delete local rows whose upstream source id is no longer present.

    Only rows that actually carry a ``source_id`` are considered — rows without
    one are locally managed and never pruned. When the source carries no ids at
    all (``matched_source_ids`` empty) nothing is deleted, so a sync can never
    wipe a section by accident.
    """
    if not matched_source_ids:
        return
    rows = db.execute(select(model).where(model.user_id == target.id)).scalars()
    for row in rows:
        if row.source_id and row.source_id not in matched_source_ids:
            db.delete(row)
    db.flush()


def sync_from_hstaff(db: Session, target, requester=None) -> dict:
    """Pull an hstaff talent profile into the local ``talent_*`` tables.

    If the target isn't hstaff-linked, or hstaff is unreachable / returns a non-200
    body, this keeps the last-known local rows and returns a ``synced=False``
    summary — it never raises.

    On a successful sync each section is a full mirror of the source: rows whose
    upstream id no longer appears are deleted, and items that are no longer
    verified upstream get their ``verified`` flag lowered again (unless the row is
    hidden/confidential — a manager-set flag is never downgraded).
    """
    from app.identity.enums import UserSource

    if target.source != UserSource.HSTAFF.value or not target.hstaff_user_id:
        return {"synced": False, "reason": "profile is not hstaff-linked"}

    try:
        status, body = hstaff.client.get_talent(target.hstaff_user_id)
    except hstaff.HstaffError:
        return {"synced": False, "reason": "hstaff unavailable"}
    if status != 200 or not isinstance(body, dict):
        return {"synced": False, "reason": f"hstaff returned {status}"}

    talent = body or {}
    touched = 0
    touched += _sync_skills(db, target, talent)
    touched += _sync_languages(db, target, talent)
    touched += _sync_projects(db, target, talent)
    touched += _sync_experience(db, target, talent)
    touched += _sync_certificates(db, target, talent)
    db.commit()
    return {"synced": True, "updated": touched}


def _sync_skills(db, target, talent: dict) -> int:
    items = _first_list(talent, "skills", "key_skills")
    if items is None:
        return 0
    existing = {
        row.name.lower(): row
        for row in db.execute(
            select(TalentSkill).where(TalentSkill.user_id == target.id)
        ).scalars()
    }
    count = 0
    matched_source_ids: set = set()
    for idx, raw in enumerate(items):
        item = _as_dict(raw, "name")
        name = (item.get("name") or "").strip()
        if not name:
            continue
        row = existing.get(name.lower())
        if row is None:
            row = TalentSkill(user_id=target.id, name=name)
            db.add(row)
            existing[name.lower()] = row
        sid = _item_id(item)
        if sid:
            row.source_id = sid
            matched_source_ids.add(sid)
        row.level = _first_str(item, "mode", "level", default="")
        row.category = _first_str(item, "category", "group", default="")
        row.display_order = idx
        if _is_verified(item):
            row.verified = True
            row.status = "verified"
        elif row.verified:
            # MINOR-8: no longer verified upstream → lower the flag again.
            row.verified = False
            row.status = "pending"
        count += 1
    db.flush()
    _prune_removed(db, TalentSkill, target, matched_source_ids)
    return count


def _sync_languages(db: Session, target, talent: dict) -> int:
    items = _first_list(talent, "languages")
    if items is None:
        return 0
    existing = {
        row.name.lower(): row
        for row in db.execute(
            select(TalentLanguage).where(TalentLanguage.user_id == target.id)
        ).scalars()
    }
    count = 0
    matched_source_ids: set = set()
    for idx, raw in enumerate(items):
        item = _as_dict(raw, "name")
        name = (item.get("name") or "").strip()
        if not name:
            continue
        row = existing.get(name.lower())
        if row is None:
            row = TalentLanguage(user_id=target.id, name=name)
            db.add(row)
            existing[name.lower()] = row
        sid = _item_id(item)
        if sid:
            row.source_id = sid
            matched_source_ids.add(sid)
        row.level = _first_str(item, "level", "proficiency", default="")
        row.display_order = idx
        count += 1
    db.flush()
    _prune_removed(db, TalentLanguage, target, matched_source_ids)
    return count


def _sync_projects(db: Session, target, talent: dict) -> int:
    items = _first_list(talent, "projects", "portfolio")
    if items is None:
        return 0
    existing = {
        row.title.lower(): row
        for row in db.execute(
            select(TalentProject).where(TalentProject.user_id == target.id)
        ).scalars()
    }
    count = 0
    matched_source_ids: set = set()
    for idx, raw in enumerate(items):
        item = _as_dict(raw, "title")
        title = (item.get("title") or "").strip()
        if not title:
            continue
        row = existing.get(title.lower())
        if row is None:
            row = TalentProject(user_id=target.id, title=title)
            db.add(row)
            existing[title.lower()] = row
        sid = _item_id(item)
        if sid:
            row.source_id = sid
            matched_source_ids.add(sid)
        row.urls = _item_urls(item)
        row.start_date = _first_str(item, "start_date", default="")
        row.end_date = _first_str(item, "end_date", default="")
        row.present = bool(item.get("present") or item.get("is_current"))
        row.bullets = _item_bullets(item)
        row.skills = _item_skills(item)
        # MAJOR-1: only take hidden/confidential from the source when it actually
        # carries the key — otherwise keep the stored local value. A manager-set
        # ``confidential=True`` must never be silently reset to False by a sync.
        if "hidden" in item:
            row.hidden = bool(item["hidden"])
        if "confidential" in item or "client_confidential" in item:
            row.confidential = bool(
                item.get("confidential") or item.get("client_confidential")
            )
        row.display_order = idx
        if _is_verified(item):
            row.verified = True
        elif not (row.hidden or row.confidential):
            # MINOR-8: re-lower verified when upstream no longer verifies the item,
            # but never touch rows a manager flagged hidden/confidential.
            row.verified = False
        count += 1
    db.flush()
    _prune_removed(db, TalentProject, target, matched_source_ids)
    return count


def _sync_experience(db: Session, target, talent: dict) -> int:
    items = _first_list(talent, "experience", "work_experience", "employment")
    if items is None:
        return 0
    existing = {
        (row.company.lower(), row.role.lower()): row
        for row in db.execute(
            select(TalentExperience).where(TalentExperience.user_id == target.id)
        ).scalars()
    }
    count = 0
    matched_source_ids: set = set()
    for idx, raw in enumerate(items):
        item = _as_dict(raw, "company")
        company = (item.get("company") or "").strip()
        role = (item.get("role") or item.get("position") or "").strip()
        if not role and not company:
            continue
        row = existing.get((company.lower(), role.lower()))
        if row is None:
            row = TalentExperience(user_id=target.id, company=company, role=role)
            db.add(row)
            existing[(company.lower(), role.lower())] = row
        sid = _item_id(item)
        if sid:
            row.source_id = sid
            matched_source_ids.add(sid)
        row.start_date = _first_str(item, "start_date", default="")
        row.end_date = _first_str(item, "end_date", default="")
        row.present = bool(item.get("present") or item.get("is_current"))
        row.description = _first_str(item, "description", "summary", default="")
        row.technologies = _item_skills(item)
        if "hidden" in item:
            row.hidden = bool(item["hidden"])
        row.display_order = idx
        if _is_verified(item):
            row.verified = True
        elif not row.hidden:
            row.verified = False
        count += 1
    db.flush()
    _prune_removed(db, TalentExperience, target, matched_source_ids)
    return count


def _sync_certificates(db: Session, target, talent: dict) -> int:
    items = _first_list(talent, "certificates", "certifications", "courses")
    if items is None:
        return 0
    existing = {
        (row.title.lower(), row.issuer.lower()): row
        for row in db.execute(
            select(TalentCertificate).where(TalentCertificate.user_id == target.id)
        ).scalars()
    }
    count = 0
    matched_source_ids: set = set()
    for idx, raw in enumerate(items):
        item = _as_dict(raw, "title")
        title = (item.get("title") or "").strip()
        if not title:
            continue
        issuer = _first_str(item, "issuer", "organization", default="")
        row = existing.get((title.lower(), issuer.lower()))
        if row is None:
            row = TalentCertificate(user_id=target.id, title=title, issuer=issuer)
            db.add(row)
            existing[(title.lower(), issuer.lower())] = row
        sid = _item_id(item)
        if sid:
            row.source_id = sid
            matched_source_ids.add(sid)
        row.issue_date = _first_str(item, "issue_date", "date", "year", default="")
        if "hidden" in item:
            row.hidden = bool(item["hidden"])
        row.display_order = idx
        if _is_verified(item):
            row.verified = True
        elif not row.hidden:
            row.verified = False
        count += 1
    db.flush()
    _prune_removed(db, TalentCertificate, target, matched_source_ids)
    return count


# ---------------------------------------------------------------------------
# Allow-list snapshot
# ---------------------------------------------------------------------------


def _role_label(target_role: str) -> str:
    return " ".join(p.capitalize() for p in (target_role or "").split("-"))


def _query_ordered(db: Session, model, target) -> list:
    return list(
        db.execute(
            select(model)
            .where(model.user_id == target.id)
            .order_by(model.display_order.asc(), model.created_at.desc())
        ).scalars()
    )


def _public_deny(h: dict, public: bool) -> set:
    """Section keys to drop from a public snapshot.

    Applies only when ``public=True``. Experience and certificates mirror hstaff
    data; when the source no longer carries the section there is nothing to back
    their local rows, so they are dropped rather than showing stale mirrors.
    """
    if not public:
        return set()
    deny: set = set()
    if _first_list(h, "experience", "work_experience", "employment") is None:
        deny.add("experience")
    if _first_list(h, "certificates", "certifications", "courses") is None:
        deny.add("certificates")
    return deny


def _build_contact(target, h: dict) -> dict:
    contact: dict = {}
    email = (target.email or "").strip()
    if email:
        contact["email"] = email
    phone = (target.phone or "").strip() or _first_str(h, "phone", default="")
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


def _academy_block(target, h: dict) -> dict | None:
    """Optional academy progress/scores — only surfaced when explicitly public."""
    if not target.academy_progress_public:
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


def allowlist_snapshot(
    db: Session, target, target_role: str, prefs=None, public: bool = False
) -> dict:
    """Build the canonical CV data dict (allow-list only).

    The CV allow-list is: summary/bio, contact, verified + non-hidden skills,
    verified + non-hidden + non-confidential projects, languages, experience and
    certificates (each filtered by its own visibility).

    Always excluded — in BOTH modes: address, office internals, day-offs, discord
    id, chats, payment info, internal notes, confidential client names, and any
    row the owner marked ``hidden=True`` or ``confidential=True`` (regardless of
    prefs), plus any non-verified item. Academy scores only surface when
    ``target.academy_progress_public`` is true.

    ``public=False`` (the owner/manager generation path): additionally applies the
    stored ``hidden_sections`` and ``project_order`` prefs.

    ``public=True`` (the public CV path): a fixed, prefs-independent view. The
    stored ``hidden_sections`` / ``project_order`` prefs are force-ignored and —
    because experience and certificates mirror hstaff data whose source may be
    unavailable — those two sections are dropped unless the source payload for the
    section is present (see ``_public_deny``).
    """
    h = target.hstaff_profile or {}
    snapshot: dict = {
        "target_role": target_role,
        "target_role_label": _role_label(target_role),
        "personal": {"full_name": target.full_name or ""},
        "summary": {"bio": _first_str(h, "bio", "summary", "about", default="")},
        "contact": _build_contact(target, h),
    }

    # In public mode enforce a fixed deny-list: sections whose upstream source data
    # isn't present are dropped (their local rows would be stale mirror data).
    public_deny = _public_deny(h, public)

    # skills: verified only (no hidden flag on the model, so visibility via verified)
    skills = [
        {
            "name": s.name,
            "level": s.level or "",
            "category": s.category or "",
        }
        for s in _query_ordered(db, TalentSkill, target)
        if s.verified
    ]
    snapshot["skills"] = skills

    # projects: verified + not hidden + not confidential
    projects = []
    for p in _query_ordered(db, TalentProject, target):
        if not p.verified or p.hidden or p.confidential:
            continue
        projects.append(
            {
                "id": str(p.id),
                "title": p.title,
                "urls": list(p.urls or []),
                "start_date": p.start_date or "",
                "end_date": p.end_date or "",
                "present": bool(p.present),
                "bullets": list(p.bullets or []),
                "skills": list(p.skills or []),
            }
        )
    snapshot["projects"] = projects

    # languages: (no verified/hidden flags — all local rows are shown)
    snapshot["languages"] = [
        {"name": lang.name, "level": lang.level or ""}
        for lang in _query_ordered(db, TalentLanguage, target)
    ]

    # experience: verified + not hidden; dropped from public output when the
    # upstream source data for the section is unavailable (see _public_deny).
    if "experience" in public_deny:
        snapshot["experience"] = []
    else:
        snapshot["experience"] = [
            {
                "id": str(e.id),
                "company": e.company or "",
                "role": e.role or "",
                "start_date": e.start_date or "",
                "end_date": e.end_date or "",
                "present": bool(e.present),
                "description": e.description or "",
                "technologies": list(e.technologies or []),
            }
            for e in _query_ordered(db, TalentExperience, target)
            if e.verified and not e.hidden
        ]

    # certificates: verified + not hidden; dropped from public output when the
    # upstream source data for the section is unavailable (see _public_deny).
    if "certificates" in public_deny:
        snapshot["certificates"] = []
    else:
        snapshot["certificates"] = [
            {
                "title": c.title or "",
                "issuer": c.issuer or "",
                "issue_date": c.issue_date or "",
            }
            for c in _query_ordered(db, TalentCertificate, target)
            if c.verified and not c.hidden
        ]

    # academy scores/progress — only when the owner made them public.
    academy = _academy_block(target, h)
    if academy:
        snapshot["academy"] = academy

    # Apply personal prefs only on the owner/manager generation path (public=False).
    if not public and prefs:
        hidden = list(prefs.hidden_sections or [])
        for section in hidden:
            snapshot.pop(section, None)
        order = list(prefs.project_order or [])
        if order:
            by_id = {p["id"]: p for p in snapshot["projects"]}
            snapshot["projects"] = [by_id[pid] for pid in order if pid in by_id] + [
                p for p in snapshot["projects"] if p["id"] not in set(order)
            ]
    return snapshot


# ---------------------------------------------------------------------------
# Profile-aware CV snapshot (approved + permitted data only)
# ---------------------------------------------------------------------------


def _profile_section_mapping(cv_section: str) -> str:
    """Map a CV section key to the corresponding ProfileShare section key."""
    return {
        "summary": "bio",
        "contact": "external_accounts",
        "skills": "skills",
        "projects": "projects",
        "languages": "languages",
        "certificates": "certifications",
    }.get(cv_section, cv_section)


def build_profile_cv_snapshot(
    db: Session, target, target_role: str, prefs=None, public: bool = False
) -> dict:
    """Build a CV snapshot from the local profile workflow data.

    Only ``verified`` profile items are included. Project visibility is
    enforced: only ``public`` and ``public_summary`` projects are included;
    ``public_summary`` projects render only their description.

    In ``public=True`` mode the result is further filtered by ``ProfileShare``:
    the profile must be published and the section must be explicitly enabled.
    Personal prefs (hidden sections, project order) are ignored for public CVs.
    """
    # Cross-domain imports inside the function to avoid circular imports.
    from app.profile.enums import ProfileSection, ReviewState, Visibility
    from app.profile.models import (
        ProfileProject,
        ProfileShare,
        ProfileSkill,
        ProfileSocialAccount,
    )

    h = target.hstaff_profile or {}
    snapshot: dict = {
        "target_role": target_role,
        "target_role_label": _role_label(target_role),
        "personal": {"full_name": target.full_name or ""},
        "summary": {"bio": _first_str(h, "bio", "summary", "about", default="")},
        "contact": _build_profile_contact(target, h, db, public),
    }

    share = None
    if public:
        share = db.execute(
            select(ProfileShare).where(ProfileShare.user_id == target.id)
        ).scalar_one_or_none()
        if not share or not share.is_published:
            # Not published: return a minimal snapshot so the public endpoint can
            # respond with 403 and never render restricted data.
            return snapshot

    def _section_visible(section: str) -> bool:
        if not public:
            return True
        if share is None:
            return False
        return share.shows(_profile_section_mapping(section))

    # Skills: verified only, from approved_snapshot.
    # Build a lookup table so project skill_ids can be resolved to names.
    skill_by_id: dict[str, dict] = {}
    for skill in db.execute(
        select(ProfileSkill)
        .where(ProfileSkill.user_id == target.id)
        .order_by(ProfileSkill.category, ProfileSkill.name)
    ).scalars():
        if skill.review_state != ReviewState.VERIFIED.value:
            continue
        content = skill.approved_snapshot or skill.snapshot()
        skill_by_id[str(skill.id)] = {
            "name": content.get("name", skill.name),
            "category": content.get("category", skill.category),
            "level": content.get("level", skill.level),
        }

    if _section_visible("skills"):
        snapshot["skills"] = list(skill_by_id.values())

    # Projects: verified + visibility-aware.
    # A CV is externally shareable, so only PUBLIC and PUBLIC_SUMMARY items
    # are included. PUBLIC_SUMMARY renders the description only (no URLs/skills).
    if _section_visible("projects"):
        projects = []
        for project in db.execute(
            select(ProfileProject)
            .where(ProfileProject.user_id == target.id)
            .order_by(ProfileProject.present.desc(), ProfileProject.start_date.desc())
        ).scalars():
            if project.review_state != ReviewState.VERIFIED.value:
                continue
            content = project.approved_snapshot or project.snapshot()
            visibility = content.get("visibility", project.visibility)
            if visibility not in (
                Visibility.PUBLIC.value,
                Visibility.PUBLIC_SUMMARY.value,
            ):
                continue

            item: dict = {
                "id": str(project.id),
                "title": content.get("title", project.title),
                "role": "",
                "start_date": content.get("start_date", project.start_date),
                "end_date": content.get("end_date", project.end_date),
                "present": bool(content.get("present", project.present)),
                "urls": [],
                "bullets": [],
                "skills": [],
            }

            if visibility == Visibility.PUBLIC_SUMMARY.value:
                summary = content.get("description", project.description) or ""
                item["description"] = summary
                if summary:
                    item["bullets"] = [summary]
            else:
                item["description"] = content.get("description", project.description)
                description = item["description"]
                if description:
                    item["bullets"] = [description]
                repo = content.get("repository_url", project.repository_url)
                demo = content.get("live_demo_url", project.live_demo_url)
                if repo:
                    item["urls"].append(repo)
                if demo:
                    item["urls"].append(demo)
                skill_ids = content.get("skill_ids") or project.skill_ids or []
                item["skills"] = [
                    skill_by_id[str(sid)]["name"]
                    for sid in skill_ids
                    if str(sid) in skill_by_id
                ]
            projects.append(item)
        snapshot["projects"] = projects

    # Languages / Experience / Certificates: continue using Talent* tables while
    # the profile module has no models for them. Filter by verified + not hidden.
    if _section_visible("languages"):
        snapshot["languages"] = [
            {"name": lang.name, "level": lang.level or ""}
            for lang in _query_ordered(db, TalentLanguage, target)
        ]

    if _section_visible("experience"):
        snapshot["experience"] = [
            {
                "id": str(e.id),
                "company": e.company or "",
                "role": e.role or "",
                "start_date": e.start_date or "",
                "end_date": e.end_date or "",
                "present": bool(e.present),
                "description": e.description or "",
                "technologies": list(e.technologies or []),
            }
            for e in _query_ordered(db, TalentExperience, target)
            if e.verified and not e.hidden
        ]

    if _section_visible("certificates"):
        snapshot["certificates"] = [
            {
                "title": c.title or "",
                "issuer": c.issuer or "",
                "issue_date": c.issue_date or "",
            }
            for c in _query_ordered(db, TalentCertificate, target)
            if c.verified and not c.hidden
        ]

    # Academy progress only when explicitly public.
    academy = _academy_block(target, h)
    if academy:
        snapshot["academy"] = academy

    # Apply personal prefs only on the owner/manager generation path.
    if not public and prefs:
        hidden = list(prefs.hidden_sections or [])
        for section in hidden:
            snapshot.pop(section, None)
        order = list(prefs.project_order or [])
        if order:
            by_id = {p["id"]: p for p in snapshot.get("projects", [])}
            snapshot["projects"] = [by_id[pid] for pid in order if pid in by_id] + [
                p for p in snapshot.get("projects", []) if p["id"] not in set(order)
            ]

    return snapshot


def _build_profile_contact(target, h: dict, db: Session, public: bool) -> dict:
    """Contact block sourced from the user row + verified profile social accounts."""
    contact: dict = {}
    email = (target.email or "").strip()
    if email:
        contact["email"] = email
    phone = (target.phone or "").strip() or _first_str(h, "phone", default="")
    if phone:
        contact["phone"] = phone

    # Social links from hstaff profile fallback.
    github = _first_str(
        h, "github", "github_url", "github_username", "github_handle", default=""
    )
    linkedin = _first_str(
        h, "linkedin", "linkedin_url", "linkedin_username", default=""
    )
    website = _first_str(h, "website", "website_url", "blog", "web", default="")

    # Verified profile social accounts override hstaff values when present.
    from app.profile.enums import ReviewState, SocialPlatform
    from app.profile.models import ProfileSocialAccount

    accounts = db.execute(
        select(ProfileSocialAccount).where(ProfileSocialAccount.user_id == target.id)
    ).scalars()
    for account in accounts:
        if account.review_state != ReviewState.VERIFIED.value:
            continue
        content = account.approved_snapshot or account.snapshot()
        platform = content.get("platform", account.platform)
        url = content.get("url", account.url)
        username = content.get("username", account.username)
        if platform == SocialPlatform.GITHUB.value and (url or username):
            github = url or f"https://github.com/{username}"
        elif platform == SocialPlatform.LINKEDIN.value and (url or username):
            linkedin = url or f"https://www.linkedin.com/in/{username}"

    if github:
        contact["github"] = github
    if linkedin:
        contact["linkedin"] = linkedin
    if website:
        contact["website"] = website
    return contact


# ---------------------------------------------------------------------------
# CV generation + history
# ---------------------------------------------------------------------------

# Mirrors the role list in the cv-generator service (app/templates.py).
# Kept here so validation can fail fast without a network round-trip.
CV_TARGET_ROLES = {
    "backend-developer",
    "frontend-developer",
    "full-stack-developer",
    "machine-learning-engineer",
    "data-scientist",
    "devops-engineer",
}


def _validate_target_role(target_role: str) -> None:
    if target_role not in CV_TARGET_ROLES:
        raise ValueError(
            f"Unknown target role '{target_role}'. Choose from: "
            + ", ".join(sorted(CV_TARGET_ROLES))
        )


def render_public(snapshot: dict) -> str:
    """Render a public (visibility-respecting) snapshot to HTML."""
    role = snapshot.get("target_role") or "backend-developer"
    html, _ = cv_client.render(snapshot, role)
    return html


def generate_cv(db: Session, target, target_role: str, requester=None, prefs=None):
    """Build the allow-listed snapshot, render it, and store a ``talent_cv`` row.

    Returns ``(cv_id: str, html: str)``. Raises ValueError for an unknown role
    or a cv-generator failure.
    """
    _validate_target_role(target_role)
    if prefs is None:
        prefs = db.execute(
            select(TalentCVPrefs).where(
                TalentCVPrefs.user_id == target.id,
                TalentCVPrefs.target_role == target_role,
            )
        ).scalar_one_or_none()
    snapshot = allowlist_snapshot(db, target, target_role, prefs, public=False)
    try:
        html, sha = cv_client.render(snapshot, target_role)
    except cv_client.CVGeneratorError as exc:
        raise ValueError(f"CV rendering failed: {exc}") from exc
    cv = TalentCV(
        user_id=target.id,
        target_role=target_role,
        snapshot=snapshot,
        html=html,
        sha256=sha,
    )
    db.add(cv)
    db.commit()
    db.refresh(cv)
    return str(cv.id), html


def generate_profile_cv(
    db: Session, target, target_role: str, requester=None, prefs=None
):
    """Build a profile-aware snapshot, render it, and store a ``talent_cv`` row.

    Same contract as ``generate_cv`` but reads approved/permitted data from the
    ``app/profile/`` workflow instead of the hstaff mirror tables.
    """
    _validate_target_role(target_role)
    if prefs is None:
        prefs = db.execute(
            select(TalentCVPrefs).where(
                TalentCVPrefs.user_id == target.id,
                TalentCVPrefs.target_role == target_role,
            )
        ).scalar_one_or_none()
    snapshot = build_profile_cv_snapshot(db, target, target_role, prefs, public=False)
    try:
        html, sha = cv_client.render(snapshot, target_role)
    except cv_client.CVGeneratorError as exc:
        raise ValueError(f"CV rendering failed: {exc}") from exc
    cv = TalentCV(
        user_id=target.id,
        target_role=target_role,
        snapshot=snapshot,
        html=html,
        sha256=sha,
    )
    db.add(cv)
    db.commit()
    db.refresh(cv)
    return str(cv.id), html


def latest_cv(db: Session, target, target_role: str | None = None):
    stmt = (
        select(TalentCV)
        .where(TalentCV.user_id == target.id)
        .order_by(TalentCV.created_at.desc())
    )
    if target_role:
        stmt = stmt.where(TalentCV.target_role == target_role)
    return db.execute(stmt.limit(1)).scalars().first()


def get_cv(db: Session, target, cv_id):
    cv_id = str(cv_id)
    return db.execute(
        select(TalentCV).where(
            TalentCV.user_id == target.id, TalentCV.id == uuid.UUID(cv_id)
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# CV prefs
# ---------------------------------------------------------------------------


def _user_project_ids(db: Session, target) -> set:
    """All project ids the owner may reorder, from both talent and profile."""
    from app.profile.models import ProfileProject

    talent_ids = {
        str(p.id)
        for p in db.execute(
            select(TalentProject).where(TalentProject.user_id == target.id)
        ).scalars()
    }
    profile_ids = {
        str(p.id)
        for p in db.execute(
            select(ProfileProject).where(ProfileProject.user_id == target.id)
        ).scalars()
    }
    return talent_ids | profile_ids


def get_prefs(db: Session, target, target_role: str):
    """Return the stored CV prefs for ``target_role`` (or ``None``)."""
    return db.execute(
        select(TalentCVPrefs).where(
            TalentCVPrefs.user_id == target.id,
            TalentCVPrefs.target_role == target_role,
        )
    ).scalar_one_or_none()


def save_prefs(
    db: Session, target, target_role: str, hidden_sections=None, project_order=None
):
    """Upsert CV layout prefs for the owner's ``target_role``.

    ``target_role`` must be a known CV template; ``hidden_sections`` must be a
    subset of the valid CV sections; ``project_order`` is sanitised to the
    target's known project ids. Raises ValueError on bad input.
    """
    if target_role not in CV_TARGET_ROLES:
        raise ValueError(
            f"Unknown target role '{target_role}'. Choose from: "
            + ", ".join(sorted(CV_TARGET_ROLES))
        )
    prefs = db.execute(
        select(TalentCVPrefs).where(
            TalentCVPrefs.user_id == target.id,
            TalentCVPrefs.target_role == target_role,
        )
    ).scalar_one_or_none()

    if hidden_sections is not None:
        if not isinstance(hidden_sections, list):
            raise ValueError("hidden_sections must be a list")
        unknown = [s for s in hidden_sections if s not in CV_SECTIONS]
        if unknown:
            raise ValueError(f"Unknown CV section(s): {', '.join(unknown)}")

    if project_order is not None:
        if not isinstance(project_order, list):
            raise ValueError("project_order must be a list")
        known = _user_project_ids(db, target)
        project_order = [str(pid) for pid in project_order if str(pid) in known]

    if prefs is None:
        prefs = TalentCVPrefs(user_id=target.id, target_role=target_role)
        db.add(prefs)
    if hidden_sections is not None:
        prefs.hidden_sections = hidden_sections
    if project_order is not None:
        prefs.project_order = project_order
    db.commit()
    db.refresh(prefs)
    return prefs
