"""Endpoints that surface the logged-in user's own hstaff data.

Only meaningful for source="hstaff" users. Reads use the user's stored hstaff
token; on expiry we refresh once and retry."""

import datetime
import json

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, get_optional_user
from app.core.responses import error as _err
from app.hstaff import client as hstaff
from app.identity import services
from app.identity.enums import UserSource
from app.identity.models import User
from app.profile.enums import ProfileSection
from app.profile.models import ProfileShare

router = APIRouter(prefix="/hstaff", tags=["hstaff"])

# hstaff rejects these at the schema level for everyone, regardless of the
# service account's permissions — reject early with a clear message.
_HSTAFF_FORBIDDEN_TYPES = {"c-level", "superadmin"}


def _ensure_hstaff(user):
    if user.source != UserSource.HSTAFF.value or not user.hstaff_access_token:
        return _err("Not an hstaff-linked account.", 400)
    return None


def _refresh_token(db, user) -> bool:
    if not user.hstaff_refresh_token:
        return False
    try:
        data = hstaff.client.refresh(user.hstaff_refresh_token)
    except hstaff.HstaffError:
        return False
    user.hstaff_access_token = data.get("access_token", user.hstaff_access_token)
    user.hstaff_refresh_token = data.get("refresh_token", user.hstaff_refresh_token)
    db.commit()
    return True


@router.post("/users/create")
def create_hstaff_user(
    body: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Register a user on hstaff via the shared HR service account.

    Access: superadmin, or the hr / academy-manager roles, or anyone holding
    `users:create`. We check the role as well as the permission because
    hstaff-linked users get their permissions from hstaff (not our catalog),
    so a role check covers HR/academy-manager regardless of login source.
    The account is created on hstaff only; hstaff emails the new user a
    verification link to set their own password."""
    allowed = (
        user.is_superuser
        or user.has_role("hr")
        or user.has_role("academy-manager")
        or "users:create" in user.permission_codenames
    )
    if not allowed:
        return _err("User creation access required", 403)

    email = (body.get("email") or "").strip().lower()
    first_name = (body.get("first_name") or "").strip()
    last_name = (body.get("last_name") or "").strip() or None
    user_type = (body.get("type") or "").strip()
    # hstaff requires these; sensible defaults so the caller can omit them.
    time_zone = (body.get("time_zone") or "Asia/Seoul").strip()
    status_val = (body.get("status") or "active").strip()
    day_offs = body.get("day_offs")
    if not email:
        return _err("email is required", 400)
    if not first_name:
        return _err("first_name is required", 400)
    if not user_type:
        return _err("type (role) is required", 400)
    if user_type in _HSTAFF_FORBIDDEN_TYPES:
        return _err("c-level and superadmin users cannot be created here", 400)

    payload = {
        "email": email,
        "first_name": first_name,
        "type": user_type,
        "time_zone": time_zone,
        "status": status_val,
    }
    if last_name:
        payload["last_name"] = last_name
    if day_offs is not None:
        payload["day_offs"] = day_offs

    try:
        status, hbody = hstaff.client.create_user(payload)
    except hstaff.HstaffError as e:
        return _err(str(e), 502)
    return JSONResponse(hbody, status_code=status)


@router.post("/users/sync")
def sync_hstaff_users(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Pull all users from hstaff and create local mirrors for new ones.

    Uses the privileged service account to call hstaff's /admin/all, then
    creates a local ``User`` row (source=hstaff) for every email that isn't
    already in our ``users`` table.

    Access: superadmin, hr, academy-manager, or anyone holding ``users:create``.
    """
    allowed = (
        user.is_superuser
        or user.has_role("hr")
        or user.has_role("academy-manager")
        or "users:create" in user.permission_codenames
    )
    if not allowed:
        return _err("User administration access required", 403)

    try:
        result = services.sync_hstaff_users(db)
    except (RuntimeError, hstaff.HstaffError) as e:
        return _err(str(e), 502)
    return result


@router.get("/users/{email}/bundle")
def hstaff_user_bundle(
    email: str,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """Full hstaff talent bundle for ANY user, fetched via the HR service account.

    Lets other members view a user's rich hstaff profile (bio, skills, languages,
    projects, work activity) — the read-only equivalent of the owner's own page.
    Available to any authenticated member, mirroring the public profile endpoint."""
    ident = (email or "").strip()
    target = db.execute(
        select(User).where(User.email.ilike(ident))
    ).scalar_one_or_none()
    if not target and "@" not in ident and ident:
        target = (
            db.execute(select(User).where(User.email.ilike(f"{ident}@%")))
            .scalars()
            .first()
        )
    if not target:
        return _err("User not found", 404)
    if target.source != UserSource.HSTAFF.value or not target.hstaff_user_id:
        return _err("Not an hstaff-linked account.", 400)

    empty = {
        "profile": None,
        "skills": [],
        "languages": [],
        "projects": [],
        "heatmap": None,
    }
    try:
        status, profile = hstaff.client.get_talent(target.hstaff_user_id)
        if status != 200 or not isinstance(profile, dict):
            return {**empty}
        talent_id = profile.get("talent_id")
        skills = languages = projects = []
        heatmap = None
        if talent_id:

            def read(path, params=None):
                s, b = hstaff.client.service_get(path, params)
                return b if s == 200 else None

            skills = read(f"/talent-skills/talent/{talent_id}") or []
            languages = read(f"/talent-languages/talent/{talent_id}") or []
            projects = read(f"/talent-projects/talent/{talent_id}") or []
            # Heatmap access may be denied for the HR account on some users — skip if so.
            heatmap = read("/activity-logs/heatmap", {"talent_id": talent_id})
    except hstaff.HstaffError as e:
        return _err(str(e), 502)

    if user is None:
        # Leak closure: anonymous visitors only see the bundle when the Academy
        # Manager has published the profile, and only the sections they exposed.
        share = db.execute(
            select(ProfileShare).where(ProfileShare.user_id == target.id)
        ).scalar_one_or_none()
        if not (share and share.is_published):
            return {**empty}
        if not share.shows(ProfileSection.BIO.value):
            # Keep identity basics, drop bio and the PII the identity router
            # never exposes anonymously (day_offs / contact info).
            profile = {
                k: v
                for k, v in (profile or {}).items()
                if k
                in (
                    "first_name",
                    "last_name",
                    "img_url",
                    "type",
                    "time_zone",
                    "status",
                    "talent_id",
                )
            }
        else:
            profile.pop("day_offs", None)
        if not share.shows(ProfileSection.SKILLS.value):
            skills = languages = []
        if not share.shows(ProfileSection.PROJECTS.value):
            projects = []

    return {
        "profile": profile,
        "skills": skills,
        "languages": languages,
        "projects": projects,
        "heatmap": heatmap,
    }


@router.get("/profile")
def profile(user: User = Depends(get_current_user)):
    denied = _ensure_hstaff(user)
    if denied:
        return denied
    return user.hstaff_profile or {}


@router.get("/analytics")
def analytics(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    denied = _ensure_hstaff(user)
    if denied:
        return denied

    def fetch(path, params=None):
        try:
            status, body = hstaff.client.get(user.hstaff_access_token, path, params)
            if status == 401 and _refresh_token(db, user):
                status, body = hstaff.client.get(user.hstaff_access_token, path, params)
            return body if status == 200 else None
        except hstaff.HstaffError:
            return None

    def kv(label, value):
        return {"label": label, "value": value}

    prof = user.hstaff_profile or {}
    today = datetime.date.today()
    start = today - datetime.timedelta(days=30)
    dash = (
        fetch(
            "dashboard/",
            {"start_date": start.isoformat(), "end_date": today.isoformat()},
        )
        or {}
    )
    monthly = (
        fetch("rise/reports/monthly", {"year": today.year, "month": today.month}) or {}
    )

    pp = dash.get("progress_plans") or []
    al = dash.get("activity_logs") or []
    stats = dash.get("statistics") or {}

    sources = [
        {
            "label": "Profile",
            "available": bool(prof),
            "items": [
                kv("Type", prof.get("type")),
                kv("Time zone", prof.get("time_zone")),
                kv("Has mentees", prof.get("has_mentees")),
                kv("Hubstaff connected", prof.get("hubstaff_connected")),
                kv("Day-offs", prof.get("day_offs")),
                kv("Discord linked", bool(prof.get("discord_user_id"))),
            ],
        },
        {
            "label": "Dashboard — deepwork & progress",
            "available": bool(dash),
            "items": [
                kv("Progress plans", len(pp)),
                kv("Activity logs (deepwork)", len(al)),
                kv("Talents tracked", len(stats)),
            ],
        },
        {
            "label": f"Monthly report ({today.year}-{today.month:02d})",
            "available": bool(monthly),
            "items": [
                kv("Categories", ", ".join(monthly.get("categories") or []) or "—"),
                kv("User rows", len(monthly.get("rows") or [])),
            ],
        },
        {
            "label": "Effective permissions",
            "available": bool(user.cached_permissions),
            "items": [kv("Count", len(user.cached_permissions or []))],
        },
    ]
    for s in sources:
        s["items"] = [i for i in s["items"] if i["value"] not in (None, "")]
    return {"sources": sources}


@router.get("/api/{path:path}")
def passthrough(
    path: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    denied = _ensure_hstaff(user)
    if denied:
        return denied

    params = dict(request.query_params)
    try:
        status, body = hstaff.client.get(user.hstaff_access_token, path, params)
        if status == 401 and _refresh_token(db, user):
            status, body = hstaff.client.get(user.hstaff_access_token, path, params)
    except hstaff.HstaffError as e:
        return _err(str(e), 502)
    return JSONResponse(body, status_code=status)


@router.api_route("/api/{path:path}", methods=["POST", "PUT", "PATCH", "DELETE"])
async def passthrough_write(
    path: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Write passthrough for the owner's own hstaff data (profile edits, skills,
    languages, projects, avatar upload/delete). Forwards under the user's hstaff
    token so hstaff's RBAC still gates every write. JSON bodies pass through as-is;
    multipart uploads are rebuilt so the avatar file reaches hstaff intact."""
    denied = _ensure_hstaff(user)
    if denied:
        return denied

    params = dict(request.query_params)
    json_body = None
    files = None
    data = None

    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        files, data = {}, {}
        for key, value in form.multi_items():
            filename = getattr(value, "filename", None)
            if filename:
                files[key] = (filename, await value.read(), value.content_type)
            else:
                data[key] = value
    else:
        raw = await request.body()
        if raw:
            try:
                json_body = json.loads(raw)
            except ValueError:
                json_body = None

    def call():
        return hstaff.client.call(
            user.hstaff_access_token,
            request.method,
            path,
            params=params,
            json=json_body,
            files=files,
            data=data,
        )

    try:
        status, body = call()
        if status == 401 and _refresh_token(db, user):
            status, body = call()
    except hstaff.HstaffError as e:
        return _err(str(e), 502)
    return JSONResponse(body, status_code=status)
