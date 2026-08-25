"""Seed the RBAC catalog (permissions + roles) and a superadmin account.

Idempotent: safe to run on every startup. Mirrors the Django management commands
seed_rbac + seed_admin.
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password, verify_password
from app.identity.enums import SUPERADMIN, UserSource
from app.identity.models import Permission, Role, User, user_roles
from app.identity.rbac_catalog import PERMISSIONS, ROLES

logger = logging.getLogger(__name__)


def seed_rbac(db: Session) -> None:
    perm_by_code: dict[str, Permission] = {}
    for codename, resource, action, description in PERMISSIONS:
        perm = db.execute(
            select(Permission).where(Permission.codename == codename)
        ).scalar_one_or_none()
        if perm is None:
            perm = Permission(codename=codename)
            db.add(perm)
        perm.resource = resource
        perm.action = action
        perm.description = description
        perm_by_code[codename] = perm
    db.flush()

    for name, cfg in ROLES.items():
        role = db.execute(select(Role).where(Role.name == name)).scalar_one_or_none()
        if role is None:
            role = Role(name=name)
            db.add(role)
        role.display_name = cfg["display_name"]
        role.description = cfg["description"]
        role.level = cfg["level"]
        role.is_system = cfg["is_system"]
        role.permissions = [
            perm_by_code[c] for c in cfg["permissions"] if c in perm_by_code
        ]

    # Remove system roles that are no longer in the catalog (e.g. ceo, coo, cto).
    # Locally-created roles (is_system=False) are preserved.
    known = set(ROLES)
    for role in db.execute(select(Role)).scalars():
        if role.name not in known and role.is_system:
            logger.info("Removing stale system role %r", role.name)
            # Detach any users whose primary role is this one.
            for u in db.execute(select(User).where(User.role_id == role.id)).scalars():
                u.role_id = None
                u.role = None
            # Remove M2M links in user_roles.
            db.execute(user_roles.delete().where(user_roles.c.role_id == role.id))
            role.permissions = []
            db.flush()
            db.delete(role)

    db.commit()
    logger.info("Seeded %d permissions and %d roles.", len(PERMISSIONS), len(ROLES))


def seed_admin(db: Session) -> None:
    seed_rbac(db)

    email = settings.ADMIN_EMAIL.strip().lower()
    password = settings.ADMIN_PASSWORD
    superadmin_role = db.execute(
        select(Role).where(Role.name == SUPERADMIN)
    ).scalar_one_or_none()

    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user:
        if not verify_password(password, user.password_hash):
            user.password_hash = hash_password(password)
            db.commit()
            logger.info("Updated admin password for %s", email)
        else:
            logger.info("Admin already exists. Skipping.")
        return

    user = User(
        email=email,
        full_name="Super Admin",
        source=UserSource.LOCAL.value,
        is_staff=True,
        is_superuser=True,
        is_active=True,
        is_verified=True,
        password_hash=hash_password(password),
    )
    db.add(user)
    db.flush()
    if superadmin_role:
        user.role = superadmin_role
        user.roles = [superadmin_role]
    db.commit()
    logger.info("Created superadmin %s", email)
