"""Onboarding pipeline logic.

Flow (see the diagram):
  1 intro_call  ┐
  2 tech_interview ├─ parallel interviews, each needs a report + pass/fail
  3 culture_fit  ┘
  4 discussion   ← unlocks when 1,2,3 all PASSED; needs a report + pass/fail
  5 payment      ← unlocks when 4 PASSED; candidate uploads proof, management approves
  6 access       ← unlocks when 5 PASSED; staff sets zoom + completes → user onboarded

A FAILED outcome at any stage rejects the whole pipeline (terminal).
"""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.files import media_url, save_upload
from app.onboarding.access import (
    INTERVIEW_ROLE_CAPS,
    can_assign,
    can_conduct,
    is_management,
    is_staff_member,
)
from app.onboarding.enums import (
    INTERVIEW_KEYS,
    ONBOARDING_STATUS_VALUES,
    REPORT_KEYS,
    STAGE_BY_KEY,
    STAGE_DEFS,
    ZOOM_KEYS,
    ChatKind,
    OnboardingStatus,
    StageOutcome,
)
from app.onboarding.models import (
    Chat,
    ChatMessage,
    OnboardingPipeline,
    SheetSource,
    Stage,
    StageReport,
)


class PipelineError(Exception):
    """Domain error (bad stage, locked stage, permission) → surfaced as 400/403."""


def _now():
    return datetime.now(timezone.utc)


# ---------- creation ----------
def ensure_stages(db: Session, pipeline: OnboardingPipeline) -> OnboardingPipeline:
    existing = {s.key for s in pipeline.stages}
    missing = [d["key"] for d in STAGE_DEFS if d["key"] not in existing]
    if missing:
        for k in missing:
            db.add(Stage(pipeline_id=pipeline.id, key=k))
        db.flush()
        db.refresh(pipeline)
    return pipeline


def get_pipeline_for(db: Session, user) -> OnboardingPipeline | None:
    """The caller's onboarding pipeline, or None if they aren't a candidate.

    Pipelines are candidate records: they are created only by the Google-Sheet
    sync (as userless leads) and linked to a user when an actual candidate first
    authenticates through hstaff (adoption). We never fabricate one for whoever
    hits this endpoint — staff / management accounts are not onboarding
    candidates and must never appear on the board."""
    pipeline = db.execute(
        select(OnboardingPipeline).where(OnboardingPipeline.user_id == user.id)
    ).scalar_one_or_none()
    if pipeline is None or is_staff_member(user):
        return None
    ensure_stages(db, pipeline)
    db.commit()
    return pipeline


def _stages_by_key(pipeline) -> dict[str, Stage]:
    return {s.key: s for s in pipeline.stages}


# ---------- assignment helpers ----------
def _is_assignee(user, stage: Stage) -> bool:
    if not user:
        return False
    return user.is_superuser or any(a.id == user.id for a in stage.assignees)


def discussion_participant_ids(pipeline, stages: dict[str, Stage] | None = None) -> set:
    stages = stages or _stages_by_key(pipeline)
    ids: set = set()
    for k in INTERVIEW_KEYS:
        for a in stages[k].assignees:
            ids.add(a.id)
    return ids


def can_access_discussion(
    user, pipeline, stages: dict[str, Stage] | None = None
) -> bool:
    if not user:
        return False
    return user.is_superuser or user.id in discussion_participant_ids(pipeline, stages)


# ---------- unlock rules ----------
def is_unlocked(pipeline, key: str, stages: dict[str, Stage] | None = None) -> bool:
    stages = stages or _stages_by_key(pipeline)
    if key in INTERVIEW_KEYS:
        return True
    if key == "discussion":
        return all(
            stages[k].outcome == StageOutcome.PASSED.value for k in INTERVIEW_KEYS
        )
    if key == "payment":
        return stages["discussion"].outcome == StageOutcome.PASSED.value
    if key == "access":
        return stages["payment"].outcome == StageOutcome.PASSED.value
    return False


# ---------- serialization ----------
def _can_act(user, key: str, stage: Stage, pipeline, stages) -> bool:
    if not user:
        return False
    if key in INTERVIEW_KEYS:
        return _is_assignee(user, stage)
    if key == "discussion":
        return can_access_discussion(user, pipeline, stages)
    if key == "payment":
        return is_management(user)
    if key == "access":
        return is_staff_member(user)
    return False


def serialize(db: Session, pipeline, viewer=None) -> dict:
    ensure_stages(db, pipeline)
    stages = _stages_by_key(pipeline)
    discussion_ok = can_access_discussion(viewer, pipeline, stages) if viewer else True

    out_stages = []
    for d in STAGE_DEFS:
        s = stages[d["key"]]
        restricted = d["key"] == "discussion" and not discussion_ok
        out_stages.append(
            {
                "key": d["key"],
                "num": d["num"],
                "label": d["label"],
                "kind": d["kind"],
                "has_zoom": d["zoom"],
                "outcome": s.outcome,
                "locked": not is_unlocked(pipeline, d["key"], stages),
                "restricted": restricted,
                "can_act": (
                    _can_act(viewer, d["key"], s, pipeline, stages) if viewer else False
                ),
                "assignees": (
                    [
                        {"id": str(a.id), "name": a.full_name or a.email}
                        for a in s.assignees
                    ]
                    if d["key"] in INTERVIEW_KEYS
                    else []
                ),
                "zoom_link": "" if restricted else s.zoom_link,
                "payment_proof_url": media_url(s.payment_proof),
                "decided_by": (
                    (s.decided_by.full_name or s.decided_by.email)
                    if s.decided_by
                    else None
                ),
                "decided_at": s.decided_at.isoformat() if s.decided_at else None,
                "reports": (
                    []
                    if restricted
                    else [
                        {
                            "author": (
                                (r.author.full_name or r.author.email)
                                if r.author
                                else "unknown"
                            ),
                            "notes": r.notes,
                            "outcome": r.outcome,
                            "created_at": r.created_at.isoformat(),
                        }
                        for r in s.reports
                    ]
                ),
            }
        )

    user = pipeline.user
    address_proof_url = media_url(user.address_proof) if user else None

    return {
        "id": str(pipeline.id),
        "status": pipeline.status,
        "rejected": pipeline.status == OnboardingStatus.REJECTED.value,
        "onboarded": pipeline.status == OnboardingStatus.ONBOARDED.value,
        "candidate": {
            "id": str(user.id) if user else None,
            "full_name": user.full_name if user else pipeline.candidate_name,
            "email": pipeline.email,
        },
        "address": user.address if user else None,
        "address_verified": user.address_verified if user else False,
        "address_proof_url": address_proof_url,
        "stages": out_stages,
        "completed_at": (
            pipeline.completed_at.isoformat() if pipeline.completed_at else None
        ),
    }


# ---------- flat editable table (HR / academy-manager) ----------
_ROW_STAGES = {
    "call": "intro_call",
    "tech": "tech_interview",
    "culture": "culture_fit",
}


def _stage_report_text(stage) -> str:
    return stage.reports[0].notes if stage.reports else ""


def serialize_row(db: Session, pipeline) -> dict:
    ensure_stages(db, pipeline)
    stages = _stages_by_key(pipeline)
    user = pipeline.user
    row = {
        "id": str(pipeline.id),
        "status": pipeline.status,
        "full_name": (user.full_name if user else pipeline.candidate_name) or "",
        "email": pipeline.email or "",
        "phone": pipeline.phone or "",
        "scholarship_pct": pipeline.scholarship_pct,
        "payment_usd": (
            str(pipeline.payment_usd) if pipeline.payment_usd is not None else ""
        ),
        "admission_fee_paid": pipeline.admission_fee_paid,
        "google_form_url": pipeline.google_form_url,
        "final_decision": pipeline.final_decision,
        "created_at": pipeline.created_at.isoformat(),
    }
    for col, key in (
        ("call", "intro_call"),
        ("tech", "tech_interview"),
        ("culture", "culture_fit"),
    ):
        a = stages[key].assignees[0] if stages[key].assignees else None
        row[f"{col}_assignee"] = (
            {"id": str(a.id), "name": a.full_name or a.email, "role": a.role_name}
            if a
            else None
        )
        row[f"{col}_report"] = _stage_report_text(stages[key])
    return row


def _set_stage_report(db: Session, stage, notes: str, actor):
    r = stage.reports[0] if stage.reports else None
    if r:
        if r.notes != notes:
            r.notes = notes
            r.author = actor
    elif notes:
        db.add(
            StageReport(
                stage_id=stage.id,
                author_id=actor.id,
                notes=notes,
                outcome=stage.outcome,
            )
        )


def update_row(db: Session, actor, pipeline, data: dict):
    from app.identity.models import User

    if not is_management(actor):
        raise PipelineError("Only onboarding managers can edit the board.")

    user = pipeline.user
    if "full_name" in data:
        name = (data["full_name"] or "").strip() or None
        if user:
            user.full_name = name
            user.name_customized = True
        else:
            pipeline.candidate_name = name
    if "phone" in data:
        phone = (data["phone"] or "").strip() or None
        if user:
            user.phone = phone
        else:
            pipeline.candidate_phone = phone

    if "status" in data:
        status = data["status"]
        if status not in ONBOARDING_STATUS_VALUES:
            raise PipelineError("Invalid status.")
        pipeline.status = status
        terminal = status in (
            OnboardingStatus.ONBOARDED.value,
            OnboardingStatus.REJECTED.value,
        )
        pipeline.completed_at = _now() if terminal else None
    if "final_decision" in data:
        decision = data["final_decision"]
        if decision not in ONBOARDING_STATUS_VALUES:
            raise PipelineError("Invalid final decision.")
        pipeline.final_decision = decision
    if "scholarship_pct" in data:
        pipeline.scholarship_pct = _parse_int(data["scholarship_pct"])
    if "payment_usd" in data:
        pipeline.payment_usd = _parse_decimal(data["payment_usd"])
    if "admission_fee_paid" in data:
        pipeline.admission_fee_paid = bool(data["admission_fee_paid"])
    if "google_form_url" in data:
        pipeline.google_form_url = (data["google_form_url"] or "").strip()

    stages = _stages_by_key(pipeline)

    assignee_fields = ("call_assignee_id", "tech_assignee_id", "culture_assignee_id")
    if any(f in data for f in assignee_fields) and not can_assign(actor):
        raise PipelineError("Only the academy manager can assign interviewers.")
    for col, key in (
        ("call", "intro_call"),
        ("tech", "tech_interview"),
        ("culture", "culture_fit"),
    ):
        field = f"{col}_assignee_id"
        if field in data:
            stage = stages[key]
            uid = (data[field] or "") if data[field] is not None else ""
            if uid:
                u = db.execute(
                    select(User).where(User.id == uid, User.is_active.is_(True))
                ).scalar_one_or_none()
                if not u:
                    raise PipelineError("Selected assignee was not found.")
                if not can_conduct(u, key):
                    raise PipelineError(
                        f"{u.full_name or u.email} must be an academy manager or HR."
                    )
                stage.assignees = [u]
            else:
                stage.assignees = []

    for col, key in _ROW_STAGES.items():
        report_field = f"{col}_report"
        if report_field in data:
            _set_stage_report(
                db, stages[key], (data[report_field] or "").strip(), actor
            )

    db.commit()
    return pipeline


def _parse_int(v):
    if v in (None, ""):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        raise PipelineError("Scholarship must be a whole number.")


def _parse_decimal(v):
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        raise PipelineError("Payment must be a number.")


# ---------- guards ----------
def _require_active(pipeline):
    if pipeline.status != OnboardingStatus.IN_PROGRESS.value:
        raise PipelineError(
            f"Pipeline is {pipeline.status}; no further actions allowed."
        )


def _get_stage(pipeline, key: str) -> Stage:
    if key not in STAGE_BY_KEY:
        raise PipelineError(f"Unknown stage: {key}")
    for s in pipeline.stages:
        if s.key == key:
            return s
    raise PipelineError(f"Unknown stage: {key}")


def _apply_outcome(db: Session, pipeline, stage, outcome, actor):
    stage.outcome = outcome
    stage.decided_by = actor
    stage.decided_at = _now()
    stage.zoom_link = ""  # the meeting is over — drop the link
    if outcome == StageOutcome.FAILED.value:
        pipeline.status = OnboardingStatus.REJECTED.value
        delete_chats(db, pipeline)


# ---------- assignment (management assigns interviewers) ----------
def eligible_assignees(db: Session, key: str) -> list:
    from app.identity.models import User

    if key not in INTERVIEW_KEYS:
        return []
    caps = INTERVIEW_ROLE_CAPS.get(key, set())
    users = db.execute(select(User).where(User.is_active.is_(True))).scalars()
    out = []
    for u in users:
        if u.is_superuser or "superadmin" in u.role_names:
            continue
        if _roles_of(u) & caps:
            out.append(u)
    return out


def _roles_of(user) -> set[str]:
    return set(user.role_names) | ({user.role_name} if user.role_name else set())


def assign_stage(
    db: Session, actor, pipeline, key: str, assignee_ids: list
) -> OnboardingPipeline:
    from app.identity.models import User

    if not can_assign(actor):
        raise PipelineError("Only the academy manager can assign interviewers.")
    if key not in INTERVIEW_KEYS:
        raise PipelineError("Only the three interview stages can be assigned.")
    _require_active(pipeline)
    stage = _get_stage(pipeline, key)
    if stage.outcome != StageOutcome.PENDING.value:
        raise PipelineError("This interview is already decided; assignment is closed.")

    users = list(
        db.execute(
            select(User).where(
                User.id.in_(assignee_ids or []), User.is_active.is_(True)
            )
        ).scalars()
    )
    for u in users:
        if not can_conduct(u, key):
            raise PipelineError(f"{u.email} isn't allowed to conduct this interview.")
    stage.assignees = users
    db.commit()
    return pipeline


# ---------- staff actions ----------
def set_zoom_link(
    db: Session, actor, pipeline, key: str, link: str
) -> OnboardingPipeline:
    if not is_staff_member(actor):
        raise PipelineError("Only staff can set a Zoom link.")
    if key not in ZOOM_KEYS:
        raise PipelineError("This stage has no Zoom link.")
    if key == "discussion" and not can_access_discussion(actor, pipeline):
        raise PipelineError("Only the assigned interviewers can access the discussion.")
    _require_active(pipeline)
    stage = _get_stage(pipeline, key)
    if key in INTERVIEW_KEYS and not _is_assignee(actor, stage):
        raise PipelineError("Only the assigned interviewer can act on this stage.")
    stage.zoom_link = (link or "").strip()
    db.commit()
    return pipeline


def submit_report(
    db: Session, actor, pipeline, key: str, notes: str, outcome: str
) -> OnboardingPipeline:
    if not is_staff_member(actor):
        raise PipelineError("Only staff can submit interview reports.")
    if key not in REPORT_KEYS:
        raise PipelineError("This stage does not take an interview report.")
    if outcome not in (StageOutcome.PASSED.value, StageOutcome.FAILED.value):
        raise PipelineError("Report outcome must be 'passed' or 'failed'.")
    if key == "discussion" and not can_access_discussion(actor, pipeline):
        raise PipelineError("Only the assigned interviewers can decide the discussion.")
    _require_active(pipeline)
    if not is_unlocked(pipeline, key):
        raise PipelineError("This stage is locked — earlier stages must pass first.")

    stage = _get_stage(pipeline, key)
    if key in INTERVIEW_KEYS and not _is_assignee(actor, stage):
        raise PipelineError("Only the assigned interviewer can act on this stage.")
    db.add(
        StageReport(
            stage_id=stage.id, author_id=actor.id, notes=notes or "", outcome=outcome
        )
    )
    _apply_outcome(db, pipeline, stage, outcome, actor)
    db.commit()
    return pipeline


def decide_payment(
    db: Session, actor, pipeline, outcome: str, notes: str = ""
) -> OnboardingPipeline:
    if not is_management(actor):
        raise PipelineError("Only management can approve payments.")
    if outcome not in (StageOutcome.PASSED.value, StageOutcome.FAILED.value):
        raise PipelineError("Decision must be 'passed' or 'failed'.")
    _require_active(pipeline)
    if not is_unlocked(pipeline, "payment"):
        raise PipelineError("Payment is locked until the discussion is passed.")
    stage = _get_stage(pipeline, "payment")
    if outcome == StageOutcome.PASSED.value and not stage.payment_proof:
        raise PipelineError("No payment proof has been uploaded yet.")
    if notes:
        db.add(
            StageReport(
                stage_id=stage.id, author_id=actor.id, notes=notes, outcome=outcome
            )
        )
    _apply_outcome(db, pipeline, stage, outcome, actor)
    db.commit()
    return pipeline


def complete_access(db: Session, actor, pipeline) -> OnboardingPipeline:
    if not is_staff_member(actor):
        raise PipelineError("Only staff can grant access.")
    _require_active(pipeline)
    if not is_unlocked(pipeline, "access"):
        raise PipelineError("Access is locked until payment is approved.")
    stage = _get_stage(pipeline, "access")
    _apply_outcome(db, pipeline, stage, StageOutcome.PASSED.value, actor)
    pipeline.status = OnboardingStatus.ONBOARDED.value
    pipeline.completed_at = _now()
    delete_chats(db, pipeline)
    db.commit()
    return pipeline


# ---------- address verification ----------
def verify_address(db: Session, actor, pipeline) -> OnboardingPipeline:
    if not is_staff_member(actor):
        raise PipelineError("Only staff can verify addresses.")
    user = pipeline.user
    if not user or not user.address_proof:
        raise PipelineError("The candidate hasn't uploaded address proof yet.")
    _require_active(pipeline)
    user.address_verified = True
    db.commit()
    return pipeline


# ---------- candidate action ----------
def upload_payment_proof(db: Session, candidate, pipeline, file) -> OnboardingPipeline:
    _require_active(pipeline)
    if not is_unlocked(pipeline, "payment"):
        raise PipelineError("Payment isn't open yet.")
    if not file:
        raise PipelineError("An image file is required.")
    stage = _get_stage(pipeline, "payment")
    stage.payment_proof = save_upload(file, "payment_proofs")
    db.commit()
    return pipeline


# ---------- chat ----------
def can_access_chat(user, pipeline, kind: str) -> bool:
    if not user:
        return False
    if user.is_superuser or is_management(user):
        return True
    if user.id in discussion_participant_ids(pipeline):
        return True
    if kind == ChatKind.CANDIDATE.value and user.id == pipeline.user_id:
        return True
    return False


def _serialize_message(m, viewer=None) -> dict:
    return {
        "id": str(m.id),
        "author": (m.author.full_name or m.author.email) if m.author else "unknown",
        "author_id": str(m.author_id) if m.author_id else None,
        "author_role": m.author.role_name if m.author else "",
        "mine": bool(viewer and m.author_id == viewer.id),
        "body": m.body,
        "created_at": m.created_at.isoformat(),
    }


def _valid_kind(kind: str) -> str:
    if kind not in (ChatKind.CANDIDATE.value, ChatKind.DISCUSSION.value):
        raise PipelineError("Unknown chat.")
    return kind


def _chat_participants(db: Session, pipeline, kind: str, chat) -> list[dict]:
    from app.identity.models import User

    ids = set(discussion_participant_ids(pipeline))
    if kind == ChatKind.CANDIDATE.value and pipeline.user_id:
        ids.add(pipeline.user_id)
    if chat:
        ids.update(m.author_id for m in chat.messages if m.author_id)
    users = db.execute(select(User).where(User.id.in_(ids))).scalars() if ids else []
    return [
        {"id": str(u.id), "name": u.full_name or u.email, "role": u.role_name}
        for u in users
        if not (u.is_superuser or u.role_name == "superadmin")
    ]


def get_chat(db: Session, user, pipeline, kind: str) -> dict:
    _valid_kind(kind)
    if not can_access_chat(user, pipeline, kind):
        raise PipelineError("You don't have access to this chat.")
    chat = next((c for c in pipeline.chats if c.kind == kind), None)
    messages = list(chat.messages) if chat else []
    return {
        "kind": kind,
        "closed": pipeline.status != OnboardingStatus.IN_PROGRESS.value,
        "participants": _chat_participants(db, pipeline, kind, chat),
        "messages": [_serialize_message(m, user) for m in messages],
    }


def post_message(db: Session, user, pipeline, kind: str, body: str) -> dict:
    _valid_kind(kind)
    if not can_access_chat(user, pipeline, kind):
        raise PipelineError("You don't have access to this chat.")
    _require_active(pipeline)
    body = (body or "").strip()
    if not body:
        raise PipelineError("Message can't be empty.")
    chat = next((c for c in pipeline.chats if c.kind == kind), None)
    if chat is None:
        chat = Chat(pipeline_id=pipeline.id, kind=kind)
        db.add(chat)
        db.flush()
    db.add(ChatMessage(chat_id=chat.id, author_id=user.id, body=body))
    db.commit()
    db.refresh(pipeline)
    return get_chat(db, user, pipeline, kind)


def delete_chats(db: Session, pipeline) -> None:
    for c in list(pipeline.chats):
        db.delete(c)
    db.flush()


# ---------- sheet → table sync ----------
_EMAIL_NEEDLES = ("email", "e-mail")
_NAME_NEEDLES = ("full name", "your name", "name")
_PHONE_NEEDLES = ("phone", "mobile", "contact number")
_TIMESTAMP_NEEDLES = ("timestamp", "submitted", "submission")


def _parse_sheet_datetime(value: str):
    value = (value or "").strip()
    if not value:
        return None
    for fmt in (
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%m/%d/%Y",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _pick_column(columns: list[str], needles: tuple[str, ...]) -> str | None:
    for c in columns:
        low = c.lower()
        if any(n in low for n in needles):
            return c
    return None


def _looks_like_email_col(rows: list[dict], col: str) -> bool:
    sample = [(r.get(col) or "").strip() for r in rows[:25]]
    filled = [v for v in sample if v]
    if not filled:
        return False
    hits = sum(1 for v in filled if "@" in v and "." in v.split("@")[-1])
    return hits >= max(1, len(filled) // 2)


def _identity_columns(
    columns: list[str], rows: list[dict]
) -> tuple[str | None, str | None, str | None]:
    email_col = _pick_column(columns, _EMAIL_NEEDLES)
    if not email_col:
        email_col = next((c for c in columns if _looks_like_email_col(rows, c)), None)
    if not email_col:
        return (
            _pick_column(columns, _NAME_NEEDLES),
            None,
            _pick_column(columns, _PHONE_NEEDLES),
        )
    i = columns.index(email_col)
    name_col = columns[i - 1] if i >= 1 else _pick_column(columns, _NAME_NEEDLES)
    phone_col = (
        columns[i + 1]
        if i + 1 < len(columns)
        else _pick_column(columns, _PHONE_NEEDLES)
    )
    return name_col, email_col, phone_col


def active_sheet(db: Session) -> tuple[str, str]:
    from app.onboarding import gsheet

    s = (
        db.execute(select(SheetSource).order_by(SheetSource.created_at.desc()))
        .scalars()
        .first()
    )
    if s:
        return s.spreadsheet_id, s.gid
    return gsheet.SHEET_ID, gsheet.SHEET_GID


def set_sheet_source(db: Session, actor, url: str) -> dict:
    from app.onboarding import gsheet

    if not is_management(actor):
        raise PipelineError("Only onboarding managers can change the sheet source.")

    sid, gid = gsheet.parse_sheet_url(url)
    payload = gsheet.fetch_rows(sid, gid)  # also validates it's readable
    cols = payload.get("columns", [])

    name_col, email_col, phone_col = _identity_columns(cols, payload.get("rows", []))
    missing = [
        label
        for label, col in (
            ("name", name_col),
            ("email", email_col),
            ("phone", phone_col),
        )
        if not col
    ]

    for s in db.execute(select(SheetSource)).scalars():
        db.delete(s)
    db.add(SheetSource(url=url.strip(), spreadsheet_id=sid, gid=gid))
    db.commit()
    payload["warning"] = (
        f"Loaded, but no {', '.join(missing)} column detected — those rows won't sync to Stages."
        if missing
        else ""
    )
    return payload


def sync_from_sheet(db: Session) -> int:
    from app.identity.models import User
    from app.onboarding import gsheet

    payload = gsheet.fetch_rows(*active_sheet(db))
    columns = payload.get("columns", [])
    name_col, email_col, phone_col = _identity_columns(columns, payload.get("rows", []))
    if not email_col:
        raise PipelineError("The sheet has no email column, so it can't be synced.")
    ts_col = _pick_column(columns, _TIMESTAMP_NEEDLES)

    by_email: dict[str, dict] = {}
    for row in payload.get("rows", []):
        email = (row.get(email_col) or "").strip().lower()
        if email:
            by_email[email] = row

    existing_user_emails = {
        e.lower()
        for (e,) in db.execute(
            select(User.email).where(User.email.in_(list(by_email.keys())))
        ).all()
    }

    created = 0
    for email, row in by_email.items():
        if email in existing_user_emails:
            continue
        name = ((row.get(name_col) or "").strip() or None) if name_col else None
        phone = ((row.get(phone_col) or "").strip() or None) if phone_col else None

        pipeline = db.execute(
            select(OnboardingPipeline).where(
                OnboardingPipeline.candidate_email == email,
                OnboardingPipeline.user_id.is_(None),
            )
        ).scalar_one_or_none()
        if pipeline is None:
            applied_at = _parse_sheet_datetime(row.get(ts_col)) if ts_col else None
            pipeline = OnboardingPipeline(
                candidate_email=email,
                candidate_name=name,
                candidate_phone=phone,
            )
            if applied_at:
                pipeline.created_at = applied_at
            db.add(pipeline)
            db.flush()
            created += 1
        else:
            if name is not None and pipeline.candidate_name != name:
                pipeline.candidate_name = name
            if pipeline.candidate_phone != phone:
                pipeline.candidate_phone = phone
        ensure_stages(db, pipeline)
    db.commit()
    return created


def list_pipelines(db: Session):
    # Best-effort: fold any new sheet rows into the table before listing.
    try:
        sync_from_sheet(db)
    except Exception:
        db.rollback()
    pipelines = db.execute(
        select(OnboardingPipeline)
        .where(OnboardingPipeline.status != OnboardingStatus.ONBOARDED.value)
        .order_by(OnboardingPipeline.created_at.desc())
    ).scalars()
    # The board is candidate-only. A pipeline linked to a staff / management
    # account is never a real candidate — exclude it (defence in depth; such a
    # pipeline shouldn't be created in the first place).
    return [p for p in pipelines if not (p.user and is_staff_member(p.user))]
