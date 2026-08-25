import uuid

from fastapi import APIRouter, Body, Depends, File, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.responses import error as _err
from app.identity.models import User
from app.onboarding import gsheet, services
from app.onboarding.access import can_assign, is_management, is_staff_member
from app.onboarding.models import OnboardingPipeline, SheetSource
from app.onboarding.services import PipelineError

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


def _get_pipeline(db, pid):
    return db.get(OnboardingPipeline, pid)


# ---------- candidate (self) ----------
# The candidate self-view. A pipeline is a candidate record sourced from the
# Google Sheet (and linked to a user only on hstaff-candidate adoption); it is
# never fabricated here. A caller with no pipeline simply isn't a candidate.
_NOT_A_CANDIDATE = "You have no onboarding record."


@router.get("/me")
def my_onboarding(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    pipeline = services.get_pipeline_for(db, user)
    if not pipeline:
        return _err(_NOT_A_CANDIDATE, 404)
    return services.serialize(db, pipeline, user)


@router.get("/me/chat")
def my_chat_get(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pipeline = services.get_pipeline_for(db, user)
    if not pipeline:
        return _err(_NOT_A_CANDIDATE, 404)
    try:
        return services.get_chat(db, user, pipeline, "candidate")
    except PipelineError as e:
        return _err(str(e), 403 if "access" in str(e) else 400)


@router.post("/me/chat")
def my_chat_post(
    body: dict = Body(default={}),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    pipeline = services.get_pipeline_for(db, user)
    if not pipeline:
        return _err(_NOT_A_CANDIDATE, 404)
    try:
        return services.post_message(
            db, user, pipeline, "candidate", body.get("body", "")
        )
    except PipelineError as e:
        return _err(str(e), 403 if "access" in str(e) else 400)


@router.post("/me/payment-proof")
def upload_payment_proof(
    image: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    pipeline = services.get_pipeline_for(db, user)
    if not pipeline:
        return _err(_NOT_A_CANDIDATE, 404)
    try:
        services.upload_payment_proof(db, user, pipeline, image)
    except PipelineError as e:
        return _err(str(e), 400)
    return services.serialize(db, pipeline, user)


# ---------- staff ----------
def _staff_only(user):
    return (
        None
        if is_staff_member(user)
        else _err("This action requires a staff role.", 403)
    )


@router.get("/candidates")
def list_candidates(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    denied = _staff_only(user)
    if denied:
        return denied
    return [services.serialize_row(db, p) for p in services.list_pipelines(db)]


@router.get("/sheet")
def sheet_candidates(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    denied = _staff_only(user)
    if denied:
        return denied
    try:
        return gsheet.fetch_rows(*services.active_sheet(db))
    except gsheet.SheetError as e:
        return _err(str(e), 502)


@router.get("/sheet/source")
def sheet_source_get(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    denied = _staff_only(user)
    if denied:
        return denied
    s = (
        db.execute(select(SheetSource).order_by(SheetSource.created_at.desc()))
        .scalars()
        .first()
    )
    return {"url": s.url if s else ""}


@router.post("/sheet/source")
def sheet_source_set(
    body: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    denied = _staff_only(user)
    if denied:
        return denied
    if not is_management(user):
        return _err("Only onboarding managers can change the sheet.", 403)
    try:
        payload = services.set_sheet_source(db, user, body.get("url", ""))
    except PipelineError as e:
        return _err(str(e), 400)
    except gsheet.SheetError as e:
        return _err(str(e), 502)
    s = (
        db.execute(select(SheetSource).order_by(SheetSource.created_at.desc()))
        .scalars()
        .first()
    )
    return {"url": s.url if s else "", **payload}


@router.post("/sync")
def sync_sheet(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not is_management(user):
        return _err("Only onboarding managers can sync the sheet.", 403)
    try:
        created = services.sync_from_sheet(db)
    except PipelineError as e:
        return _err(str(e), 400)
    except gsheet.SheetError as e:
        return _err(str(e), 502)
    return {"created": created}


@router.get("/interviewers")
def interviewers(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    denied = _staff_only(user)
    if denied:
        return denied
    return [
        {
            "id": str(u.id),
            "name": u.full_name or u.email,
            "email": u.email,
            "role": u.role_name,
        }
        for u in services.eligible_assignees(db, "intro_call")
    ]


@router.patch("/candidates/{pid}/row")
def update_candidate_row(
    pid: uuid.UUID,
    body: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not is_management(user):
        return _err("Only onboarding managers can edit the board.", 403)
    pipeline = _get_pipeline(db, pid)
    if not pipeline:
        return _err("Not found", 404)
    try:
        services.update_row(db, user, pipeline, body)
    except PipelineError as e:
        return _err(str(e), 400)
    return services.serialize_row(db, pipeline)


@router.get("/candidates/{pid}")
def candidate_detail(
    pid: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    denied = _staff_only(user)
    if denied:
        return denied
    pipeline = _get_pipeline(db, pid)
    if not pipeline:
        return _err("Not found", 404)
    services.ensure_stages(db, pipeline)
    return services.serialize(db, pipeline, user)


# ---------- chats ----------
@router.get("/candidates/{pid}/chats/{kind}")
def candidate_chat_get(
    pid: uuid.UUID,
    kind: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    denied = _staff_only(user)
    if denied:
        return denied
    pipeline = _get_pipeline(db, pid)
    if not pipeline:
        return _err("Not found", 404)
    try:
        return services.get_chat(db, user, pipeline, kind)
    except PipelineError as e:
        return _err(str(e), 403 if "access" in str(e) else 400)


@router.post("/candidates/{pid}/chats/{kind}")
def candidate_chat_post(
    pid: uuid.UUID,
    kind: str,
    body: dict = Body(default={}),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    denied = _staff_only(user)
    if denied:
        return denied
    pipeline = _get_pipeline(db, pid)
    if not pipeline:
        return _err("Not found", 404)
    try:
        return services.post_message(db, user, pipeline, kind, body.get("body", ""))
    except PipelineError as e:
        return _err(str(e), 403 if "access" in str(e) else 400)


# ---------- stage actions ----------
@router.get("/candidates/{pid}/stages/{key}/eligible")
def eligible_assignees(
    pid: uuid.UUID,
    key: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not can_assign(user):
        return _err("Only the academy manager can assign interviewers.", 403)
    return [
        {
            "id": str(u.id),
            "name": u.full_name or u.email,
            "email": u.email,
            "role": u.role_name,
        }
        for u in services.eligible_assignees(db, key)
    ]


_PERMISH = (
    "role",
    "management",
    "academy manager",
    "staff",
    "assigned interviewer",
    "access the discussion",
)


def _run(db, request, user, pid, fn):
    pipeline = _get_pipeline(db, pid)
    if not pipeline:
        return _err("Not found", 404)
    try:
        fn(pipeline)
    except PipelineError as e:
        msg = str(e)
        status = 403 if any(w in msg for w in _PERMISH) else 400
        return _err(msg, status)
    return services.serialize(db, pipeline, user)


@router.post("/candidates/{pid}/stages/{key}/assign")
def assign_stage(
    pid: uuid.UUID,
    key: str,
    request: Request,
    body: dict = Body(default={}),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _run(
        db,
        request,
        user,
        pid,
        lambda p: services.assign_stage(db, user, p, key, body.get("assignees", [])),
    )


@router.patch("/candidates/{pid}/stages/{key}/zoom")
def set_zoom(
    pid: uuid.UUID,
    key: str,
    request: Request,
    body: dict = Body(default={}),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _run(
        db,
        request,
        user,
        pid,
        lambda p: services.set_zoom_link(db, user, p, key, body.get("zoom_link", "")),
    )


@router.post("/candidates/{pid}/stages/{key}/report")
def submit_report(
    pid: uuid.UUID,
    key: str,
    request: Request,
    body: dict = Body(default={}),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _run(
        db,
        request,
        user,
        pid,
        lambda p: services.submit_report(
            db, user, p, key, body.get("notes", ""), body.get("outcome")
        ),
    )


@router.post("/candidates/{pid}/payment/decision")
def decide_payment(
    pid: uuid.UUID,
    request: Request,
    body: dict = Body(default={}),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _run(
        db,
        request,
        user,
        pid,
        lambda p: services.decide_payment(
            db, user, p, body.get("outcome"), body.get("notes", "")
        ),
    )


@router.post("/candidates/{pid}/access/complete")
def complete_access(
    pid: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _run(db, request, user, pid, lambda p: services.complete_access(db, user, p))


@router.post("/candidates/{pid}/address/verify")
def verify_address(
    pid: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _run(db, request, user, pid, lambda p: services.verify_address(db, user, p))
