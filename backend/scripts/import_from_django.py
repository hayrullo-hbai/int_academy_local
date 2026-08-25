"""Import users, roles and permissions from the Django backend into this DB.

The Django project and this one share the same RBAC catalog, so a fresh install
already has identical roles + permissions (see app/seed.py). This script is for
migrating *real data* — the users (and any custom roles) you actually created in
the Django app — preserving primary keys, role assignments and password hashes.

Usage
-----
1. In the Django project, export the identity app to JSON:

       python manage.py dumpdata identity.permission identity.role identity.user \
           --indent 2 > django_dump.json

2. Point this project at its database (.env / DATABASE_URL) and run:

       python -m scripts.import_from_django path/to/django_dump.json

It is idempotent: rows are matched by primary key and updated in place, so it is
safe to re-run. Django "unusable" passwords (hstaff users, prefixed "!") are
stored as NULL. Migrated local-user hashes keep working thanks to the
django_pbkdf2_sha256 scheme registered in app.core.security.
"""

import json
import sys

from app.core.database import Base, SessionLocal, engine
from app.identity.enums import UserSource
from app.identity.models import Permission, Role, User


def _load(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise SystemExit("Expected a Django dumpdata JSON array.")
    return data


def _by_model(objects: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for obj in objects:
        out.setdefault(obj.get("model", ""), []).append(obj)
    return out


def _upsert(db, model, pk, defaults: dict):
    row = db.get(model, pk)
    if row is None:
        row = model(id=pk)
        db.add(row)
    for k, v in defaults.items():
        setattr(row, k, v)
    return row


def run(path: str) -> None:
    Base.metadata.create_all(bind=engine)
    objects = _load(path)
    grouped = _by_model(objects)
    db = SessionLocal()
    n_perm = n_role = n_user = 0
    try:
        # 1. Permissions (independent).
        for obj in grouped.get("identity.permission", []):
            f = obj["fields"]
            _upsert(
                db,
                Permission,
                obj["pk"],
                {
                    "codename": f["codename"],
                    "resource": f.get("resource", ""),
                    "action": f.get("action", ""),
                    "description": f.get("description", ""),
                },
            )
            n_perm += 1
        db.flush()

        # 2. Roles + their permission sets (perms already loaded above).
        perm_by_pk = {p.id: p for p in db.query(Permission).all()}
        for obj in grouped.get("identity.role", []):
            f = obj["fields"]
            role = _upsert(
                db,
                Role,
                obj["pk"],
                {
                    "name": f["name"],
                    "display_name": f.get("display_name", ""),
                    "description": f.get("description", ""),
                    "level": f.get("level", 100),
                    "is_system": f.get("is_system", False),
                },
            )
            role.permissions = [
                perm_by_pk[p] for p in f.get("permissions", []) if p in perm_by_pk
            ]
            n_role += 1
        db.flush()

        # 3. Users + role FK + roles M2M + password hash.
        role_by_pk = {r.id: r for r in db.query(Role).all()}
        for obj in grouped.get("identity.user", []):
            f = obj["fields"]
            pwd = f.get("password") or ""
            # Django marks unusable passwords with a leading "!".
            password_hash = None if (not pwd or pwd.startswith("!")) else pwd
            user = _upsert(
                db,
                User,
                obj["pk"],
                {
                    "email": f["email"],
                    "full_name": f.get("full_name"),
                    "name_customized": f.get("name_customized", False),
                    "phone": f.get("phone"),
                    "password_hash": password_hash,
                    "source": f.get("source", UserSource.LOCAL.value),
                    "hstaff_user_id": f.get("hstaff_user_id"),
                    "hstaff_access_token": f.get("hstaff_access_token"),
                    "hstaff_refresh_token": f.get("hstaff_refresh_token"),
                    "cached_permissions": f.get("cached_permissions") or [],
                    "hstaff_profile": f.get("hstaff_profile"),
                    "status": f.get("status", "active"),
                    "time_zone": f.get("time_zone", "Asia/Tashkent"),
                    "office_location": f.get("office_location", "tashkent_uz"),
                    "address": f.get("address"),
                    "address_verified": f.get("address_verified", False),
                    "img_url": f.get("img_url"),
                    "is_active": f.get("is_active", True),
                    "is_staff": f.get("is_staff", False),
                    "is_superuser": f.get("is_superuser", False),
                    "is_verified": f.get("is_verified", False),
                    "must_change_password": f.get("must_change_password", False),
                },
            )
            user.role_id = f.get("role")  # FK (uuid or None)
            user.roles = [role_by_pk[r] for r in f.get("roles", []) if r in role_by_pk]
            n_user += 1

        db.commit()
        print(f"Imported {n_perm} permissions, {n_role} roles, {n_user} users.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(
            "Usage: python -m scripts.import_from_django <django_dump.json>",
            file=sys.stderr,
        )
        raise SystemExit(2)
    run(sys.argv[1])
