import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, BaseModel
from app.onboarding.enums import OnboardingStatus, StageOutcome

stage_assignees = Table(
    "onboarding_stage_assignees",
    Base.metadata,
    Column(
        "stage_id",
        UUID(as_uuid=True),
        ForeignKey("onboarding_stages.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "user_id",
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class SheetSource(BaseModel):
    """Singleton-ish: the Google Sheet the onboarding board reads from."""

    __tablename__ = "onboarding_sheet_source"

    url: Mapped[str] = mapped_column(String(500), default="")
    spreadsheet_id: Mapped[str] = mapped_column(String(128))
    gid: Mapped[str] = mapped_column(String(32), default="")


class OnboardingPipeline(BaseModel):
    """One interview/onboarding pipeline per candidate.

    A candidate starts life as a *userless* pipeline: a raw lead pulled from the
    Google Sheet. Only when they first authenticate through hstaff is a `User`
    created and adopted onto the pipeline."""

    __tablename__ = "onboarding_pipelines"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=True,
    )
    candidate_email: Mapped[str | None] = mapped_column(
        String(254), nullable=True, index=True
    )
    candidate_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    candidate_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default=OnboardingStatus.IN_PROGRESS.value
    )
    final_decision: Mapped[str] = mapped_column(String(20), default="")
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    scholarship_pct: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    payment_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    admission_fee_paid: Mapped[bool] = mapped_column(Boolean, default=False)
    google_form_url: Mapped[str] = mapped_column(String(500), default="")

    user = relationship("User")
    stages = relationship(
        "Stage", back_populates="pipeline", cascade="all, delete-orphan"
    )
    chats = relationship(
        "Chat", back_populates="pipeline", cascade="all, delete-orphan"
    )

    # ---- identity helpers (user when linked, else the candidate_* fields) ----
    @property
    def email(self) -> str | None:
        return self.user.email if self.user_id else self.candidate_email

    @property
    def display_name(self) -> str | None:
        if self.user_id:
            return self.user.full_name or self.user.email
        return self.candidate_name or self.candidate_email

    @property
    def phone(self) -> str | None:
        return self.user.phone if self.user_id else self.candidate_phone


class Stage(BaseModel):
    """A single stage of a pipeline (intro_call, tech_interview, … access)."""

    __tablename__ = "onboarding_stages"
    __table_args__ = (UniqueConstraint("pipeline_id", "key"),)

    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("onboarding_pipelines.id", ondelete="CASCADE")
    )
    key: Mapped[str] = mapped_column(String(32))
    outcome: Mapped[str] = mapped_column(String(10), default=StageOutcome.PENDING.value)
    zoom_link: Mapped[str] = mapped_column(String(500), default="")
    payment_proof: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )  # media path

    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    pipeline = relationship("OnboardingPipeline", back_populates="stages")
    decided_by = relationship("User", foreign_keys=[decided_by_id])
    assignees = relationship("User", secondary=stage_assignees)
    reports = relationship(
        "StageReport",
        back_populates="stage",
        cascade="all, delete-orphan",
        order_by="StageReport.created_at",
    )


class StageReport(BaseModel):
    """An interviewer's written report on a stage. Kept on-platform for audit."""

    __tablename__ = "onboarding_stage_reports"

    stage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("onboarding_stages.id", ondelete="CASCADE")
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str] = mapped_column(Text, default="")
    outcome: Mapped[str] = mapped_column(String(10), default=StageOutcome.PASSED.value)

    stage = relationship("Stage", back_populates="reports")
    author = relationship("User", foreign_keys=[author_id])


class Chat(BaseModel):
    """A conversation attached to a pipeline (candidate / discussion)."""

    __tablename__ = "onboarding_chats"
    __table_args__ = (UniqueConstraint("pipeline_id", "kind"),)

    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("onboarding_pipelines.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(16))

    pipeline = relationship("OnboardingPipeline", back_populates="chats")
    messages = relationship(
        "ChatMessage",
        back_populates="chat",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )


class ChatMessage(BaseModel):
    __tablename__ = "onboarding_chat_messages"

    chat_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("onboarding_chats.id", ondelete="CASCADE")
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    body: Mapped[str] = mapped_column(Text)

    chat = relationship("Chat", back_populates="messages")
    author = relationship("User", foreign_keys=[author_id])
