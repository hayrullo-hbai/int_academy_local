import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, BaseModel
from app.identity.enums import (
    OfficeLocation,
    UserSource,
    UserStatus,
)

# ---- association tables ----
role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column(
        "role_id",
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "permission_id",
        UUID(as_uuid=True),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

user_roles = Table(
    "user_roles",
    Base.metadata,
    Column(
        "user_id",
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "role_id",
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Permission(BaseModel):
    """A single RBAC permission, mirroring hstaff (resource:action)."""

    __tablename__ = "permissions"

    codename: Mapped[str] = mapped_column(String(100), unique=True)
    resource: Mapped[str] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(String(255), default="")

    roles = relationship(
        "Role", secondary=role_permissions, back_populates="permissions"
    )


class Role(BaseModel):
    """RBAC role. `level` mirrors hstaff's hierarchy (0 = superadmin, highest)."""

    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(50), unique=True)
    display_name: Mapped[str] = mapped_column(String(100), default="")
    description: Mapped[str] = mapped_column(String(255), default="")
    level: Mapped[int] = mapped_column(Integer, default=100)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    permissions = relationship(
        "Permission", secondary=role_permissions, back_populates="roles"
    )
    creator = relationship("User", foreign_keys=[created_by_id])

    @property
    def permission_codenames(self) -> list[str]:
        return [p.codename for p in self.permissions]


class User(BaseModel):
    __tablename__ = "users"

    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Set when the user edits their own name here. Once true, an hstaff re-sync
    # will NOT overwrite full_name (their local choice wins).
    name_customized: Mapped[bool] = mapped_column(Boolean, default=False)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    phone: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # `role` is the active/primary role; `roles` is the full granted set.
    role_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="SET NULL"), nullable=True
    )
    role = relationship("Role", foreign_keys=[role_id])
    roles = relationship("Role", secondary=user_roles)

    source: Mapped[str] = mapped_column(String(10), default=UserSource.LOCAL.value)
    hstaff_user_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    hstaff_access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    hstaff_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    cached_permissions: Mapped[list] = mapped_column(JSONB, default=list)
    hstaff_profile: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    status: Mapped[str] = mapped_column(String(20), default=UserStatus.ACTIVE.value)
    time_zone: Mapped[str] = mapped_column(String(64), default="Asia/Tashkent")
    office_location: Mapped[str] = mapped_column(
        String(16), default=OfficeLocation.TASHKENT.value
    )
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    address_proof: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )  # media path
    address_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    img_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # When true, this user's Academy progress (solved problems / data problems /
    # exams) is visible to any logged-in user, not just themselves and academy
    # managers/examiners (who can always see it).
    academy_progress_public: Mapped[bool] = mapped_column(Boolean, default=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_staff: Mapped[bool] = mapped_column(Boolean, default=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)

    # ---- role / permission helpers ----
    @property
    def role_name(self) -> str:
        return self.role.name if self.role else ""

    @property
    def role_names(self) -> list[str]:
        # The active/primary role always counts as granted, even if it wasn't
        # added to the M2M set.
        names = {r.name for r in self.roles}
        if self.role_id and self.role:
            names.add(self.role.name)
        return sorted(names)

    def has_role(self, name: str) -> bool:
        return self.role_name == name or name in self.role_names

    def has_usable_password(self) -> bool:
        return bool(self.password_hash)

    @property
    def permission_codenames(self) -> list[str]:
        """Effective permissions. For hstaff users these come straight from
        hstaff's /permissions/me (cached at login); for local users they derive
        from roles."""
        if self.source == UserSource.HSTAFF.value and self.cached_permissions:
            return sorted(self.cached_permissions)
        codes: set[str] = set()
        for r in self.roles:
            codes.update(p.codename for p in r.permissions)
        if self.role:
            codes.update(p.codename for p in self.role.permissions)
        return sorted(codes)


class RefreshToken(BaseModel):
    """Single-use, rotated refresh token (SHA-256 stored, never the raw value)."""

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PasswordResetToken(BaseModel):
    __tablename__ = "password_reset_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class MenteeRecord(BaseModel):
    """A mentee's email mirrored from hstaff, keyed by their mentor's local user.

    hstaff owns the mentor↔mentee relationship; we keep a thin local mirror so
    the mentor role and the mentee list survive even when hstaff is unreachable
    (see ``identity.services.sync_mentor_role``, which refreshes this at login).
    One row per (mentor, mentee email)."""

    __tablename__ = "mentee_records"
    __table_args__ = (
        UniqueConstraint("mentor_id", "mentee_email", name="uq_mentee_record_pair"),
    )

    mentor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    mentee_email: Mapped[str] = mapped_column(String(255), index=True)

    mentor = relationship("User", foreign_keys=[mentor_id])
