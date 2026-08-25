import uuid

from fastapi import APIRouter, Body, Depends, File, Response, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, get_optional_user
from app.core.files import media_url, save_upload
from app.core.responses import error as _err
from app.core.utils import resolve_user_by_ident
from app.hstaff import client as hstaff
from app.identity import services
from app.identity.enums import OFFICE_LOCATION_VALUES, SUPERADMIN, UserSource
from app.identity.models import Permission, Role, User

# for hiding sections for anonymous users
from app.profile.models import ProfileShare
from app.profile.enums import ProfileSection

router = APIRouter(tags=["identity"])


def _is_superadmin(u) -> bool:
    return bool(u.is_superuser or u.has_role(SUPERADMIN))


def _user_payload(u) -> dict:
    return {
        "id": str(u.id),
        "email": u.email,
        "full_name": u.full_name,
        "source": u.source,
        "hstaff_user_id": u.hstaff_user_id,
        "role": u.role_name,
        "roles": u.role_names,
        "permissions": u.permission_codenames,
        "is_verified": u.is_verified,
        "office_location": u.office_location,
        "address": u.address,
        "address_verified": u.address_verified,
        "academy_progress_public": u.academy_progress_public,
    }


# ---------- auth ----------
@router.post("/auth/login")
def login(body: dict = Body(...), db: Session = Depends(get_db)):
    result = services.login_user(db, body.get("email"), body.get("password"))
    if not result:
        return _err("Invalid email or password", 401)
    return result


@router.post("/auth/refresh")
def refresh(body: dict = Body(...), db: Session = Depends(get_db)):
    try:
        result = services.refresh_token(db, body.get("refresh_token"))
    except ValueError as e:
        return _err(str(e), 401)
    if not result:
        return _err("Invalid or expired refresh token", 401)
    return result


@router.post("/auth/logout")
def logout(body: dict = Body(default={}), db: Session = Depends(get_db)):
    services.logout_user(db, body.get("refresh_token"))
    return Response(status_code=204)


@router.post("/auth/forgot-password")
def forgot_password(body: dict = Body(...), db: Session = Depends(get_db)):
    services.request_password_reset(db, body.get("email"))
    return {"detail": "If that email exists, a reset link has been sent."}


@router.post("/auth/reset-password")
def reset_password(body: dict = Body(...), db: Session = Depends(get_db)):
    try:
        services.confirm_password_reset(db, body["token"], body["new_password"])
    except (ValueError, KeyError) as e:
        return _err(str(e) or "Invalid request", 400)
    return {"detail": "Password updated. Please log in."}


@router.get("/auth/me")
def me(user: User = Depends(get_current_user)):
    return _user_payload(user)


@router.patch("/auth/me/profile")
def update_my_profile(
    body: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    name = (body.get("full_name") or "").strip()
    if not name:
        return _err("Name can't be empty", 400)
    user.full_name = name
    user.name_customized = True
    db.commit()
    return _user_payload(user)


@router.patch("/auth/me/address")
def update_my_address(
    body: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    address = (body.get("address") or "").strip()
    if address:
        user.address = address
    office_location = body.get("office_location")
    if office_location and office_location in OFFICE_LOCATION_VALUES:
        user.office_location = office_location
    db.commit()
    return _user_payload(user)


@router.post("/auth/me/address-proof")
def upload_address_proof(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not file:
        return _err("A file is required", 400)
    user.address_proof = save_upload(file, "address_proofs")
    db.commit()
    url = media_url(user.address_proof)
    payload = {k: v for k, v in _user_payload(user).items() if k != "address_proof"}
    return {**payload, "address_proof_url": url}


@router.patch("/auth/me/active-role")
def switch_active_role(
    body: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        services.set_active_role(db, user, body.get("role"))
    except ValueError as e:
        return _err(str(e), 400)
    return _user_payload(user)


# ---------- public profile ----------
def _hstaff_talent_for(u, db) -> dict:
    """Live hstaff talent profile for a user, fetched via the HR service account
    so anyone viewing the profile sees it — not just the profile owner.

    Falls back to the cached ``hstaff_profile`` when the user isn't hstaff-linked
    or the live fetch fails. On a successful fetch we refresh the cached copy so
    the profile still renders if hstaff is briefly unreachable."""
    if u.source != UserSource.HSTAFF.value or not u.hstaff_user_id:
        return u.hstaff_profile or {}
    try:
        status, body = hstaff.client.get_talent(u.hstaff_user_id)
    except hstaff.HstaffError:
        return u.hstaff_profile or {}
    if status == 200 and isinstance(body, dict):
        u.hstaff_profile = body
        db.commit()
        return body
    return u.hstaff_profile or {}


def _public_profile_payload(u, talent: dict | None = None) -> dict:
    talent = talent or (u.hstaff_profile or {})
    is_hstaff = u.source == UserSource.HSTAFF.value
    # Prefer live hstaff values, fall back to our mirrored row.
    first = (talent.get("first_name") or "").strip()
    last = (talent.get("last_name") or "").strip()
    hstaff_name = (first + " " + last).strip()
    return {
        "id": str(u.id),
        "email": u.email,
        "full_name": (hstaff_name or u.full_name) if is_hstaff else u.full_name,
        "source": u.source,
        "role": u.role_name,
        "roles": u.role_names,
        "img_url": (talent.get("img_url") if is_hstaff else None) or u.img_url or "",
        "time_zone": (talent.get("time_zone") if is_hstaff else None) or u.time_zone,
        "office_location": u.office_location,
        "address": u.address,
        "address_verified": u.address_verified,
        "bio": talent.get("bio", "") if is_hstaff else "",
        # hstaff talent detail extras (only meaningful for hstaff-linked users).
        "hstaff_type": talent.get("type") if is_hstaff else None,
        "day_offs": talent.get("day_offs") if is_hstaff else None,
        "talent_id": talent.get("talent_id") if is_hstaff else None,
        "is_verified": u.is_verified,
        "academy_progress_public": u.academy_progress_public,
        "created_at": u.created_at.isoformat(),
    }


@router.get("/profile/{email}")
def public_profile(
    email: str,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    ident = (email or "").strip()
    u = db.execute(select(User).where(User.email.ilike(ident))).scalar_one_or_none()
    if not u and "@" not in ident and ident:
        u = (
            db.execute(select(User).where(User.email.ilike(f"{ident}@%")))
            .scalars()
            .first()
        )
    # Superadmins are hidden from everyone *else* — but not from themselves.
    # Blanket-404ing them meant a signed-in superadmin got "User not found" on
    # their own profile, which also took the page header (and its account
    # controls) down with it.
    if not u or (_is_superadmin(u) and not (user and user.id == u.id)):
        return _err("User not found", 404)
    talent = _hstaff_talent_for(u, db)
    payload = _public_profile_payload(u, talent)
    if user is None:
        share = db.execute(
            select(ProfileShare).where(ProfileShare.user_id == u.id)
        ).scalar_one_or_none()
        payload["is_published"] = bool(share and share.is_published)
        if not (share and share.is_published):
            # Leak closure: an unpublished profile shows nothing to the public —
            # not even that the account exists as a public profile. The
            # frontend renders the "profile is private" state from this shell.
            payload["full_name"] = ""
            payload["img_url"] = ""
            payload["bio"] = ""
            for field in (
                "email",
                "office_location",
                "address",
                "address_verified",
                "day_offs",
                "talent_id",
                "hstaff_type",
                "role",
                "roles",
                "time_zone",
            ):
                payload.pop(field, None)
            return payload
        if not share.shows(ProfileSection.BIO.value):
            payload["bio"] = ""
        for field in (
            "email",
            "office_location",
            "address",
            "address_verified",
            "day_offs",
            "talent_id",
        ):
            payload.pop(field, None)  # PII stays anonymous-only
    return payload


# ---------- roles / permissions ----------
@router.get("/roles")
def list_roles(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [
        _role_payload(r, user)
        for r in db.execute(select(Role).where(Role.name != SUPERADMIN)).scalars()
    ]


@router.get("/permissions")
def list_permissions(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return [
        {
            "codename": p.codename,
            "resource": p.resource,
            "action": p.action,
            "description": p.description,
        }
        for p in db.execute(select(Permission)).scalars()
    ]


def _role_payload(r: Role, requester: User | None = None) -> dict:
    payload = {
        "id": str(r.id),
        "name": r.name,
        "display_name": r.display_name,
        "description": r.description,
        "level": r.level,
        "is_system": r.is_system,
        "permissions": r.permission_codenames,
    }
    if requester and _is_superadmin(requester):
        payload["created_by"] = {
            "id": str(r.created_by_id) if r.created_by_id else None,
            "full_name": r.creator.full_name if r.creator else None,
        }
        payload["created_at"] = r.created_at.isoformat() if r.created_at else None
    return payload


@router.post("/roles")
def create_role(
    body: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    denied = _require_management(user)
    if denied:
        return denied
    try:
        role = services.create_role(
            db,
            name=body.get("name"),
            display_name=body.get("display_name"),
            description=body.get("description", ""),
            level=body.get("level", 50),
            permission_codenames=body.get("permissions", []),
            requester=user,
        )
    except ValueError as e:
        return _err(str(e), 400)
    return JSONResponse(_role_payload(role, user), status_code=201)


@router.put("/roles/{rid}")
def update_role(
    rid: uuid.UUID,
    body: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    denied = _require_management(user)
    if denied:
        return denied
    try:
        role = services.update_role(
            db,
            rid,
            display_name=body.get("display_name"),
            description=body.get("description"),
            level=body.get("level"),
            permission_codenames=body.get("permissions"),
            requester=user,
        )
    except ValueError as e:
        return _err(str(e), 400)
    return _role_payload(role, user)


@router.delete("/roles/{rid}")
def delete_role(
    rid: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    denied = _require_management(user)
    if denied:
        return denied
    try:
        services.delete_role(db, rid, user)
    except ValueError as e:
        return _err(str(e), 400)
    return Response(status_code=204)


# ---------- admin: user management ----------
def _admin_user_payload(u) -> dict:
    return {
        "id": str(u.id),
        "email": u.email,
        "full_name": u.full_name,
        "phone": u.phone,
        # Mirrored from hstaff at login/sync; "" when the user has no picture,
        # so the directory can fall back to initials.
        "img_url": u.img_url or "",
        "source": u.source,
        "hstaff_user_id": u.hstaff_user_id,
        "status": u.status,
        "role": u.role_name,
        "roles": u.role_names,
        "office_location": u.office_location,
        "address": u.address,
        "address_verified": u.address_verified,
        "is_active": u.is_active,
        "is_verified": u.is_verified,
        "is_superuser": u.is_superuser,
        "created_at": u.created_at.isoformat(),
    }


def _require_management(user):
    return (
        None
        if services.is_management(user)
        else _err("Management access required", 403)
    )


def _require_user_admin(user):
    return (
        None
        if services.is_user_admin(user)
        else _err("User administration access required", 403)
    )


@router.post("/users/create")
def create_user(
    body: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    denied = _require_user_admin(user)
    if denied:
        return denied
    if user.has_role("academy-manager") and not services._is_elevated(user):
        return _err("Academy managers can't create users", 403)
    try:
        created = services.create_user(
            db,
            email=body.get("email"),
            full_name=body.get("full_name"),
            password=body.get("password"),
            phone=body.get("phone"),
            office_location=body.get("office_location"),
            role_names=body.get("roles"),
            requester=user,
        )
    except ValueError as e:
        return _err(str(e), 400)
    return JSONResponse(_admin_user_payload(created), status_code=201)


@router.get("/users")
def list_users(
    source: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Any authenticated user may view the user list; only user admins may mutate.
    stmt = select(User).order_by(User.created_at.desc())
    users = [u for u in db.execute(stmt).scalars() if not _is_superadmin(u)]
    if source:
        users = [u for u in users if u.source == source]
    return [_admin_user_payload(u) for u in users]


def _get_user(db, uid):
    return db.get(User, uid)


@router.patch("/users/{uid}/roles")
def update_user_roles(
    uid: uuid.UUID,
    body: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    denied = _require_user_admin(user)
    if denied:
        return denied
    target = _get_user(db, uid)
    if not target:
        return _err("User not found", 404)
    try:
        services.set_user_roles(
            db, target, body.get("roles"), body.get("primary"), user
        )
    except ValueError as e:
        return _err(str(e), 400)
    return _admin_user_payload(target)


@router.patch("/users/{uid}")
def update_user(
    uid: uuid.UUID,
    body: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    denied = _require_user_admin(user)
    if denied:
        return denied
    target = _get_user(db, uid)
    if not target:
        return _err("User not found", 404)
    try:
        if "is_active" in body:
            services.set_user_active(db, target, body.get("is_active"), user)
    except ValueError as e:
        return _err(str(e), 400)
    return _admin_user_payload(target)


@router.delete("/users/{uid}/delete")
def delete_user(
    uid: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    denied = _require_user_admin(user)
    if denied:
        return denied
    target = _get_user(db, uid)
    if not target:
        return _err("User not found", 404)
    try:
        services.delete_user(db, target, user)
    except ValueError as e:
        return _err(str(e), 400)
    return Response(status_code=204)
