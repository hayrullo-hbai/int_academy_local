import uuid

import requests
from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.academy import judge0, mlrunner, services
from app.academy.access import can_access_academy, is_academy_admin
from app.academy.models import (
    DataProblem,
    DataSubmission,
    Dataset,
    Notebook,
    NotebookSubmission,
    Exam,
    ExamAttempt,
    ExamInvite,
    Problem,
    Question,
    Submission,
)
from app.academy.schemas import (
    AnswerIn,
    DataBlockCheckIn,
    DataSubmitIn,
    DatasetOut,
    ExamAnswerIn,
    ExamAttemptOut,
    ExamAttemptSubmitIn,
    ExamInviteIn,
    ExamListOut,
    ExamReadOut,
    ExamSubmissionOut,
    ExamSubmitIn,
    ExamWriteIn,
    MLRunIn,
    NotebookIn,
    NotebookListOut,
    NotebookOut,
    NotebookRunCellIn,
    NotebookSubmitIn,
    NotebookUpdateIn,
    ProblemCreateIn,
    ProblemDetailOut,
    ProblemListOut,
    ProblemUpdateIn,
    RunIn,
    SubmissionOut,
    SubmitIn,
    UserSearchResult,
    VisibilityIn,
)
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.files import save_upload
from app.core.responses import error as _err
from sqlalchemy import select
from app.identity.models import User

router = APIRouter(prefix="/academy", tags=["academy"])


def _require_admin(user: User):
    return (
        None
        if is_academy_admin(user)
        else _err("This action requires an academy admin role.", 403)
    )


def _require_owner_or_admin(entity, user: User):
    """Allow if user is academy admin or the entity's owner/uploader."""
    if is_academy_admin(user):
        return None
    owner_id = getattr(entity, "owner_id", None) or getattr(
        entity, "uploaded_by_id", None
    )
    if owner_id is not None and owner_id == user.id:
        return None
    return _err("This action requires an academy admin role or ownership.", 403)


def _require_access(user: User):
    """Academy is open only to learner tiers (school/softlanding/foundation/talent),
    the examiner role, and academy admins."""
    return (
        None
        if can_access_academy(user)
        else _err("You don't have access to the Academy.", 403)
    )


# ---------- problems ----------
def _problem_out(problem: Problem, user: User) -> ProblemDetailOut:
    is_owner = _is_owner(problem, user)
    can_see_secret = is_owner or is_academy_admin(user)
    return ProblemDetailOut(
        id=problem.id,
        slug=problem.slug,
        title=problem.title,
        description=problem.description,
        difficulty=problem.difficulty,
        language=problem.language,
        starter_code=problem.starter_code,
        reference_solution=problem.reference_solution if can_see_secret else "",
        sample_test_cases=services.sample_test_cases(problem),
        test_cases=list(problem.test_cases) if can_see_secret else [],
        is_owner=is_owner,
    )


@router.get("/problems", response_model=list[ProblemListOut])
def list_problems(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    if (err := _require_access(user)) is not None:
        return err
    problems = db.query(Problem).order_by(Problem.created_at).all()
    return [
        ProblemListOut(
            id=p.id,
            slug=p.slug,
            title=p.title,
            difficulty=p.difficulty,
            language=p.language,
            is_owner=_is_owner(p, user),
        )
        for p in problems
    ]


@router.get("/problems/{slug}", response_model=ProblemDetailOut)
def get_problem(
    slug: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    if (err := _require_access(user)) is not None:
        return err
    problem = db.query(Problem).filter(Problem.slug == slug).first()
    if not problem:
        return _err("Not found.", 404)
    return _problem_out(problem, user)


@router.post("/problems")
def create_problem(
    data: ProblemCreateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if (err := _require_admin(user)) is not None:
        return err
    if not data.test_cases:
        return _err("Add at least one test case.", 400)
    if not any(tc.is_sample for tc in data.test_cases):
        return _err(
            "Mark at least one test case as a sample so solvers can see an example.",
            400,
        )
    data.slug = services.unique_slug(db, Problem, data.title)
    if (
        v := services.verify_reference_solution(
            data.language, data.reference_solution, data.test_cases
        )
    ) is not None:
        return _err(*v)
    problem = services.create_problem(db, data, owner_id=user.id)
    return ProblemDetailOut(
        id=problem.id,
        slug=problem.slug,
        title=problem.title,
        description=problem.description,
        difficulty=problem.difficulty,
        language=problem.language,
        starter_code=problem.starter_code,
        sample_test_cases=services.sample_test_cases(problem),
        test_cases=list(problem.test_cases),
        is_owner=True,
    )


@router.put("/problems/{slug}", response_model=ProblemDetailOut)
def update_problem(
    slug: str,
    data: ProblemUpdateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    problem = db.query(Problem).filter(Problem.slug == slug).first()
    if not problem:
        return _err("Not found.", 404)
    if (err := _require_owner_or_admin(problem, user)) is not None:
        return err
    if not data.test_cases:
        return _err("Add at least one test case.", 400)
    if not any(tc.is_sample for tc in data.test_cases):
        return _err(
            "Mark at least one test case as a sample so solvers can see an example.",
            400,
        )
    if (
        v := services.verify_reference_solution(
            data.language, data.reference_solution, data.test_cases
        )
    ) is not None:
        return _err(*v)
    problem = services.update_problem(db, problem, data)
    return _problem_out(problem, user)


@router.delete("/problems/{slug}")
def delete_problem(
    slug: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    problem = db.query(Problem).filter(Problem.slug == slug).first()
    if not problem:
        return _err("Not found.", 404)
    if (err := _require_owner_or_admin(problem, user)) is not None:
        return err
    db.delete(problem)
    db.commit()
    return JSONResponse(status_code=204, content=None)


@router.post("/problems/{slug}/submit", response_model=SubmissionOut)
def submit_problem(
    slug: str,
    data: SubmitIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if (err := _require_access(user)) is not None:
        return err
    problem = db.query(Problem).filter(Problem.slug == slug).first()
    if not problem:
        return _err("Not found.", 404)
    if not data.source_code.strip():
        return _err("Source code cannot be empty.", 400)
    submission, http_status = services.submit_solution(
        db, problem, data.source_code, data.language, user=user
    )
    return JSONResponse(
        status_code=http_status,
        content=_submission_json(submission),
    )


def _submission_json(s: Submission) -> dict:
    return {
        "id": str(s.id),
        "problem_id": str(s.problem_id),
        "language": s.language,
        "source_code": s.source_code,
        "status": s.status,
        "passed": s.passed,
        "total": s.total,
        "detail": s.detail,
        "created_at": s.created_at.isoformat(),
    }


@router.get("/submissions", response_model=list[SubmissionOut])
def list_submissions(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    if (err := _require_access(user)) is not None:
        return err
    return db.query(Submission).order_by(Submission.created_at.desc()).all()


@router.get("/submissions/{submission_id}", response_model=SubmissionOut)
def get_submission(
    submission_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if (err := _require_access(user)) is not None:
        return err
    submission = db.get(Submission, submission_id)
    if not submission:
        return _err("Not found.", 404)
    return submission


@router.delete("/submissions/{submission_id}")
def delete_submission(
    submission_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if (err := _require_admin(user)) is not None:
        return err
    submission = db.get(Submission, submission_id)
    if not submission:
        return _err("Not found.", 404)
    db.delete(submission)
    db.commit()
    return JSONResponse(status_code=204, content=None)


# ---------- playground ----------
@router.post("/run")
def run_code(data: RunIn, user: User = Depends(get_current_user)):
    if (err := _require_access(user)) is not None:
        return err
    try:
        result = judge0.run_once(data.language, data.source_code, data.stdin)
    except requests.RequestException as exc:
        return _err(f"Judge0 request failed: {exc}", 502)
    return result


# ---------- datasets ----------
def _dataset_out(d: Dataset) -> DatasetOut:
    return DatasetOut(
        id=d.id,
        slug=d.slug,
        name=d.name,
        description=d.description,
        columns=services.dataset_columns(d),
    )


@router.get("/datasets", response_model=list[DatasetOut])
def list_datasets(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    if (err := _require_access(user)) is not None:
        return err
    return [_dataset_out(d) for d in db.query(Dataset).order_by(Dataset.name).all()]


@router.get("/datasets/{slug}", response_model=DatasetOut)
def get_dataset(
    slug: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    if (err := _require_access(user)) is not None:
        return err
    dataset = db.query(Dataset).filter(Dataset.slug == slug).first()
    if not dataset:
        return _err("Not found.", 404)
    return _dataset_out(dataset)


@router.post("/datasets", response_model=DatasetOut)
def create_dataset(
    name: str = Form(...),
    slug: str = Form(""),
    description: str = Form(""),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if (err := _require_admin(user)) is not None:
        return err
    # Slug auto-made from the given slug (if any) or the name, kept unique.
    slug = services.unique_slug(db, Dataset, slug or name)
    rel_path = save_upload(file, "datasets")
    dataset = Dataset(
        name=name,
        slug=slug,
        description=description,
        file_path=rel_path,
        uploaded_by_id=user.id,
    )
    db.add(dataset)
    db.commit()
    db.refresh(dataset)
    return _dataset_out(dataset)


@router.delete("/datasets/{slug}")
def delete_dataset(
    slug: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    dataset = db.query(Dataset).filter(Dataset.slug == slug).first()
    if not dataset:
        return _err("Not found.", 404)
    if (err := _require_owner_or_admin(dataset, user)) is not None:
        return err
    db.delete(dataset)
    db.commit()
    return JSONResponse(status_code=204, content=None)


@router.post("/ml-run")
def ml_run(
    data: MLRunIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    if (err := _require_access(user)) is not None:
        return err
    code = services.echo_last_expression(data.code)
    dataset = None
    if data.dataset:
        dataset = db.query(Dataset).filter(Dataset.slug == data.dataset).first()
        if not dataset:
            return _err("Not found.", 404)
    # Any load_dataset("slug") in the code is embedded too; `dataset` (if picked)
    # is bound to `df` for the single-dataset playground flow.
    loader = services.build_dataset_loader(db, data.code, default_dataset=dataset)
    try:
        return mlrunner.run(loader + "\n" + code, "")
    except requests.RequestException as exc:
        return _err(f"ML runner request failed: {exc}", 502)


# ---------- data problems (notebook-based, authored by admins, graded on submit) ----------
def _can_author_notebook(nb: Notebook, user: User) -> bool:
    """Academy admins (academy-manager / superadmin / examiner) and the owner may
    author, see solutions, and check. Everyone else only solves and submits."""
    return is_academy_admin(user) or nb.owner_id == user.id


def _notebook_out(nb: Notebook, user: User) -> dict:
    return services.notebook_detail(
        nb,
        is_owner=(nb.owner_id == user.id),
        include_solution=_can_author_notebook(nb, user),
    )


@router.get("/notebooks", response_model=list[NotebookListOut])
def list_notebooks(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    if (err := _require_access(user)) is not None:
        return err
    q = db.query(Notebook)
    # Authors see everything; everyone else only public data problems.
    if not is_academy_admin(user):
        q = q.filter((Notebook.is_public.is_(True)) | (Notebook.owner_id == user.id))
    rows = q.order_by(Notebook.updated_at.desc()).all()
    return [
        NotebookListOut(
            id=n.id,
            slug=n.slug,
            title=n.title,
            is_owner=(n.owner_id == user.id),
            is_public=n.is_public,
        )
        for n in rows
    ]


@router.post("/notebooks/run-cell")
def run_notebook_cell(
    data: NotebookRunCellIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Run the notebook up to (and including) the code cell at `index` and return
    that cell's output — lets solvers test their code before submitting."""
    if (err := _require_access(user)) is not None:
        return err
    cells = [c.model_dump() for c in data.cells]
    if (
        data.index < 0
        or data.index >= len(cells)
        or cells[data.index].get("kind") != "code"
    ):
        return _err("That cell can't be run.", 400)
    dataset = None
    if data.dataset:
        dataset = db.query(Dataset).filter(Dataset.slug == data.dataset).first()
        if not dataset:
            return _err("Not found.", 404)
    try:
        return services.run_notebook_cell(
            db, cells, data.index, default_dataset=dataset
        )
    except requests.RequestException as exc:
        return _err(f"ML runner request failed: {exc}", 502)


@router.post("/notebooks/check-cell")
def check_notebook_cell(
    data: NotebookRunCellIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Auto-check a code cell against its solution — authors only (it needs the
    solution, which solvers never receive)."""
    if (err := _require_admin(user)) is not None:
        return err
    cells = [c.model_dump() for c in data.cells]
    if (
        data.index < 0
        or data.index >= len(cells)
        or cells[data.index].get("kind") != "code"
    ):
        return _err("That cell can't be checked.", 400)
    dataset = None
    if data.dataset:
        dataset = db.query(Dataset).filter(Dataset.slug == data.dataset).first()
        if not dataset:
            return _err("Not found.", 404)
    try:
        return services.check_notebook_cell(
            db, cells, data.index, default_dataset=dataset
        )
    except requests.RequestException as exc:
        return _err(f"ML runner request failed: {exc}", 502)


@router.post("/notebooks", response_model=NotebookOut)
def create_notebook(
    data: NotebookIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if (err := _require_admin(user)) is not None:
        return err
    data.slug = services.unique_slug(db, Notebook, data.title)
    nb = services.create_notebook(db, data, owner_id=user.id)
    return _notebook_out(nb, user)


@router.get("/notebooks/{slug}", response_model=NotebookOut)
def get_notebook(
    slug: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    if (err := _require_access(user)) is not None:
        return err
    nb = db.query(Notebook).filter(Notebook.slug == slug).first()
    if not nb:
        return _err("Not found.", 404)
    # A private data problem is only visible to its author (admins / owner).
    if not nb.is_public and not _can_author_notebook(nb, user):
        return _err("Not found.", 404)
    # Solvers get the notebook with solutions stripped; authors see everything.
    return _notebook_out(nb, user)


@router.put("/notebooks/{slug}", response_model=NotebookOut)
def update_notebook(
    slug: str,
    data: NotebookUpdateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    nb = db.query(Notebook).filter(Notebook.slug == slug).first()
    if not nb:
        return _err("Not found.", 404)
    if (err := _require_owner_or_admin(nb, user)) is not None:
        return err
    nb = services.update_notebook(db, nb, data)
    return _notebook_out(nb, user)


@router.delete("/notebooks/{slug}")
def delete_notebook(
    slug: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    nb = db.query(Notebook).filter(Notebook.slug == slug).first()
    if not nb:
        return _err("Not found.", 404)
    if (err := _require_owner_or_admin(nb, user)) is not None:
        return err
    db.delete(nb)
    db.commit()
    return JSONResponse(status_code=204, content=None)


@router.post("/notebooks/{slug}/submit")
def submit_notebook(
    slug: str,
    data: NotebookSubmitIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """A solver submits their filled cells; every graded cell is auto-checked and
    the scored submission is stored."""
    if (err := _require_access(user)) is not None:
        return err
    nb = db.query(Notebook).filter(Notebook.slug == slug).first()
    if not nb:
        return _err("Not found.", 404)
    if not nb.is_public and not _can_author_notebook(nb, user):
        return _err("Not found.", 404)
    if not any(a.source.strip() for a in data.answers):
        return _err("Fill in the code cells before submitting.", 400)
    submission, http_status = services.submit_notebook(db, nb, data.answers, user=user)
    return JSONResponse(
        status_code=http_status,
        content=services.notebook_submission_detail(
            submission, include_grades=_can_author_notebook(nb, user)
        ),
    )


@router.get("/notebooks/{slug}/submissions")
def list_notebook_submissions(
    slug: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Authors (admins / owner) see every submission; a solver sees only their own."""
    if (err := _require_access(user)) is not None:
        return err
    nb = db.query(Notebook).filter(Notebook.slug == slug).first()
    if not nb:
        return _err("Not found.", 404)
    author = _can_author_notebook(nb, user)
    q = db.query(NotebookSubmission).filter(NotebookSubmission.notebook_id == nb.id)
    if not author:
        q = q.filter(NotebookSubmission.user_id == user.id)
    subs = q.order_by(NotebookSubmission.created_at.desc()).all()
    return [
        services.notebook_submission_detail(
            s, include_email=author, include_grades=author
        )
        for s in subs
    ]


@router.get("/notebooks/{slug}/submissions/{submission_id}")
def get_notebook_submission(
    slug: str,
    submission_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """One submission in full — the learner's code and each cell's output/error.
    Authors see any submission (with grades); a solver only their own."""
    if (err := _require_access(user)) is not None:
        return err
    nb = db.query(Notebook).filter(Notebook.slug == slug).first()
    if not nb:
        return _err("Not found.", 404)
    sub = (
        db.query(NotebookSubmission)
        .filter(
            NotebookSubmission.id == submission_id,
            NotebookSubmission.notebook_id == nb.id,
        )
        .first()
    )
    if not sub:
        return _err("Not found.", 404)
    author = _can_author_notebook(nb, user)
    if not author and sub.user_id != user.id:
        return _err("Not found.", 404)
    return services.notebook_submission_detail(
        sub, include_email=author, include_grades=author
    )


def _can_view_exam(exam: Exam, user: User) -> bool:
    """Owner/admin can always see; others need published + access check."""
    if _is_owner(exam, user) or is_academy_admin(user):
        return True
    if not exam.is_published:
        return False
    if exam.access_type == "public":
        return True
    # private — must be invited
    return any(inv.email == user.email for inv in exam.invites)


# ---------- exams ----------
@router.get("/exams", response_model=list[ExamListOut])
def list_exams(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if (err := _require_access(user)) is not None:
        return err
    exams = db.query(Exam).order_by(Exam.updated_at.desc()).all()
    return [
        ExamListOut(
            id=e.id,
            slug=e.slug,
            title=e.title,
            description=e.description,
            is_published=e.is_published,
            access_type=e.access_type,
            question_count=len(e.questions),
            is_owner=_is_owner(e, user),
            invited_emails=(
                [inv.email for inv in e.invites]
                if _is_owner(e, user) or is_academy_admin(user)
                else []
            ),
        )
        for e in exams
        if _can_view_exam(e, user)
    ]


def _is_owner(entity, user: User) -> bool:
    owner_id = getattr(entity, "owner_id", None) or getattr(
        entity, "uploaded_by_id", None
    )
    return owner_id is not None and owner_id == user.id


def _exam_read(exam: Exam, user: User) -> ExamReadOut:
    is_owner = _is_owner(exam, user)
    can_edit = is_owner or is_academy_admin(user)
    return ExamReadOut(
        id=exam.id,
        slug=exam.slug,
        title=exam.title,
        description=exam.description,
        is_published=exam.is_published,
        access_type=exam.access_type,
        questions=[
            services.question_read_payload(q, include_correct=can_edit)
            for q in exam.questions
        ],
        is_owner=is_owner,
        invited_emails=[inv.email for inv in exam.invites] if can_edit else [],
    )


@router.get("/exams/{slug}", response_model=ExamReadOut)
def get_exam(
    slug: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    if (err := _require_access(user)) is not None:
        return err
    exam = db.query(Exam).filter(Exam.slug == slug).first()
    if not exam:
        return _err("Not found.", 404)
    if not _can_view_exam(exam, user):
        return _err("Not found.", 404)
    return _exam_read(exam, user)


@router.post("/exams", response_model=ExamReadOut)
def create_exam(
    data: ExamWriteIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if (err := _require_admin(user)) is not None:
        return err
    data.slug = services.unique_slug(db, Exam, data.title)
    try:
        exam = services.create_exam(db, data, owner_id=user.id)
    except services.AcademyError as exc:
        return _err(str(exc), 400)
    return _exam_read(exam, user)


@router.put("/exams/{slug}", response_model=ExamReadOut)
def update_exam(
    slug: str,
    data: ExamWriteIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    exam = db.query(Exam).filter(Exam.slug == slug).first()
    if not exam:
        return _err("Not found.", 404)
    if (err := _require_owner_or_admin(exam, user)) is not None:
        return err
    try:
        exam = services.update_exam(db, exam, data)
    except services.AcademyError as exc:
        return _err(str(exc), 400)
    return _exam_read(exam, user)


@router.delete("/exams/{slug}")
def delete_exam(
    slug: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    exam = db.query(Exam).filter(Exam.slug == slug).first()
    if not exam:
        return _err("Not found.", 404)
    if (err := _require_owner_or_admin(exam, user)) is not None:
        return err
    db.delete(exam)
    db.commit()
    return JSONResponse(status_code=204, content=None)


@router.post("/exams/{slug}/invites")
def add_exam_invite(
    slug: str,
    data: ExamInviteIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    exam = db.query(Exam).filter(Exam.slug == slug).first()
    if not exam:
        return _err("Not found.", 404)
    if (err := _require_owner_or_admin(exam, user)) is not None:
        return err
    email = data.email.strip().lower()
    if not email or "@" not in email:
        return _err("Invalid email address.", 400)
    existing = (
        db.query(ExamInvite)
        .filter(ExamInvite.exam_id == exam.id, ExamInvite.email == email)
        .first()
    )
    if existing:
        return _err("Already invited.", 400)
    invite = ExamInvite(exam_id=exam.id, email=email)
    db.add(invite)
    db.commit()
    return {"email": email, "status": "invited"}


@router.delete("/exams/{slug}/invites")
def remove_exam_invite(
    slug: str,
    data: ExamInviteIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    exam = db.query(Exam).filter(Exam.slug == slug).first()
    if not exam:
        return _err("Not found.", 404)
    if (err := _require_owner_or_admin(exam, user)) is not None:
        return err
    email = data.email.strip().lower()
    invite = (
        db.query(ExamInvite)
        .filter(ExamInvite.exam_id == exam.id, ExamInvite.email == email)
        .first()
    )
    if not invite:
        return _err("Not invited.", 404)
    db.delete(invite)
    db.commit()
    return {"email": email, "status": "removed"}


@router.get("/users/search", response_model=list[UserSearchResult])
def search_users(
    q: str = "", user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Search users by email or name. Available to any logged-in user so exam
    owners can find people to invite to private exams. Returns at most 20
    matches."""
    if (err := _require_access(user)) is not None:
        return err
    from sqlalchemy import or_

    term = f"%{q.strip()}%"
    stmt = (
        select(User)
        .where(or_(User.email.ilike(term), User.full_name.ilike(term)))
        .limit(20)
    )
    return [
        {"id": str(u.id), "email": u.email, "full_name": u.full_name}
        for u in db.execute(stmt).scalars()
    ]


@router.get("/exams/{slug}/submissions")
def list_exam_submissions(
    slug: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    exam = db.query(Exam).filter(Exam.slug == slug).first()
    if not exam:
        return _err("Not found.", 404)
    if (err := _require_owner_or_admin(exam, user)) is not None:
        return err
    attempts = (
        db.query(ExamAttempt)
        .filter(ExamAttempt.exam_id == exam.id)
        .order_by(ExamAttempt.updated_at.desc())
        .all()
    )
    from app.identity.models import User as IdentityUser

    # Reference solutions per question — attached for the examiner only.
    qmap = {str(q.id): q for q in exam.questions}
    refs = {qid: services.exam_question_reference(q) for qid, q in qmap.items()}
    result = []
    for a in attempts:
        u = db.get(IdentityUser, a.user_id) if a.user_id else None
        enriched = []
        for entry in a.answers or []:
            e = dict(entry)
            ref = refs.get(str(entry.get("question_id")))
            if ref:
                e["reference"] = ref
            enriched.append(e)
        result.append(
            {
                "id": str(a.id),
                "user_email": u.email if u else "unknown",
                "user_name": u.full_name if u else None,
                "score": a.score,
                "max_score": a.max_score,
                "percentage": services.exam_percentage(a.score, a.max_score),
                "answers": enriched,
                "submitted_at": a.updated_at.isoformat(),
            }
        )
    return result


@router.post("/questions/{question_id}/check")
def check_answer(
    question_id: uuid.UUID,
    data: AnswerIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if (err := _require_access(user)) is not None:
        return err
    question = db.get(Question, question_id)
    if not question or question.type != "mcq":
        return _err("Not found.", 404)
    if question.exam and not _can_view_exam(question.exam, user):
        return _err("Not found.", 404)
    result = services.check_answer(question, data.selected)
    attempt = None
    if question.exam:
        try:
            attempt = services.upsert_question_grade(
                db, user, question.exam, question_id, data
            )
        except Exception:
            pass
    if attempt:
        result["attempt"] = services.exam_attempt_payload(attempt)
    return result


@router.post("/questions/{question_id}/answer")
def save_question_answer(
    question_id: uuid.UUID,
    data: ExamAnswerIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Save a single question's answer and grade it, updating the user's exam attempt."""
    if (err := _require_access(user)) is not None:
        return err
    question = db.get(Question, question_id)
    if not question or not question.exam:
        return _err("Not found or not part of an exam.", 404)
    if not _can_view_exam(question.exam, user):
        return _err("Not found.", 404)
    try:
        attempt = services.upsert_question_grade(
            db, user, question.exam, question_id, data
        )
    except services.AcademyError as exc:
        return _err(str(exc), 400)
    return services.exam_attempt_payload(attempt)


@router.post("/exams/{slug}/submit", response_model=ExamAttemptOut)
def submit_exam(
    slug: str,
    data: ExamAttemptSubmitIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Submit a whole exam. Grades every question and appends a new attempt.
    Multiple submissions are allowed — each creates a fresh row keyed by time.
    Scores are hidden from the student (only ``submitted`` is returned)."""
    if (err := _require_access(user)) is not None:
        return err
    exam = db.query(Exam).filter(Exam.slug == slug).first()
    if not exam or not _can_view_exam(exam, user):
        return _err("Not found.", 404)
    try:
        attempt = services.submit_exam_attempt(db, user, exam, data.answers)
    except services.AcademyError as exc:
        return _err(str(exc), 409)
    return services.exam_attempt_payload(attempt, hide_scores=True)


@router.get("/exams/{slug}/attempt", response_model=ExamAttemptOut)
def get_my_exam_attempt(
    slug: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """The current user's latest attempt for this exam, if any. Scores hidden."""
    if (err := _require_access(user)) is not None:
        return err
    exam = db.query(Exam).filter(Exam.slug == slug).first()
    if not exam or not _can_view_exam(exam, user):
        return _err("Not found.", 404)
    attempt = (
        db.query(ExamAttempt)
        .filter(ExamAttempt.user_id == user.id, ExamAttempt.exam_id == exam.id)
        .order_by(ExamAttempt.updated_at.desc())
        .first()
    )
    if not attempt:
        return _err("No attempt yet.", 404)
    return services.exam_attempt_payload(attempt, hide_scores=True)


# ---------- per-user progress & visibility ----------
@router.patch("/me/visibility")
def set_my_academy_visibility(
    data: VisibilityIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user.academy_progress_public = data.public
    db.commit()
    return {"academy_progress_public": user.academy_progress_public}


@router.get("/progress/{email}")
def user_progress(
    email: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """A user's Academy progress. Visible to the owner and to academy
    managers/examiners always; to other logged-in users only when the owner has
    made it public. Exam per-question breakdowns are shown only to the owner and
    academy admins."""
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

    is_owner = target.id == user.id
    privileged = is_owner or is_academy_admin(user)
    if not privileged and not target.academy_progress_public:
        return _err("This user's academy progress is private.", 403)

    # Coding problems — aggregate submissions per problem.
    prob_rows = (
        db.query(Submission, Problem)
        .join(Problem, Submission.problem_id == Problem.id)
        .filter(Submission.user_id == target.id)
        .all()
    )
    problems: dict = {}
    for sub, prob in prob_rows:
        p = problems.setdefault(
            prob.slug,
            {
                "slug": prob.slug,
                "title": prob.title,
                "difficulty": prob.difficulty,
                "language": prob.language,
                "solved": False,
                "attempts": 0,
                "last_submitted_at": None,
            },
        )
        p["attempts"] += 1
        if sub.status == "accepted":
            p["solved"] = True
        ts = sub.created_at.isoformat()
        if p["last_submitted_at"] is None or ts > p["last_submitted_at"]:
            p["last_submitted_at"] = ts

    # Data problems — aggregate submissions per data problem.
    data_rows = (
        db.query(DataSubmission, DataProblem)
        .join(DataProblem, DataSubmission.data_problem_id == DataProblem.id)
        .filter(DataSubmission.user_id == target.id)
        .all()
    )
    data_problems: dict = {}
    for sub, prob in data_rows:
        p = data_problems.setdefault(
            prob.slug,
            {
                "slug": prob.slug,
                "title": prob.title,
                "difficulty": prob.difficulty,
                "solved": False,
                "attempts": 0,
                "last_submitted_at": None,
            },
        )
        p["attempts"] += 1
        if sub.status == "passed":
            p["solved"] = True
        ts = sub.created_at.isoformat()
        if p["last_submitted_at"] is None or ts > p["last_submitted_at"]:
            p["last_submitted_at"] = ts

    # Exams — one attempt per exam.
    attempts = db.query(ExamAttempt).filter(ExamAttempt.user_id == target.id).all()
    exams = [
        services.exam_attempt_payload(a, include_breakdown=privileged) for a in attempts
    ]

    return {
        "email": target.email,
        "full_name": target.full_name,
        "is_owner": is_owner,
        "privileged": privileged,
        "public": target.academy_progress_public,
        "problems": sorted(problems.values(), key=lambda x: x["title"]),
        "data_problems": sorted(data_problems.values(), key=lambda x: x["title"]),
        "exams": sorted(exams, key=lambda x: x["exam_title"]),
    }


@router.post(
    "/questions/{question_id}/exam-problem/submit", response_model=ExamSubmissionOut
)
def submit_exam_problem(
    question_id: uuid.UUID,
    data: ExamSubmitIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if (err := _require_access(user)) is not None:
        return err
    question = db.get(Question, question_id)
    if not question or question.type != "code" or not question.exam_problem:
        return _err("Not found.", 404)
    if question.exam and not _can_view_exam(question.exam, user):
        return _err("Not found.", 404)
    if not data.source_code.strip():
        return _err("Source code cannot be empty.", 400)
    submission, http_status = services.submit_exam_problem(
        db, question.exam_problem, data.source_code, data.language
    )
    # Live-grade: update the user's exam attempt for this question.
    attempt = None
    if question.exam and http_status == 200:
        try:
            attempt = services.upsert_question_grade(
                db, user, question.exam, question_id, data
            )
        except Exception:
            pass  # grading is best-effort during a run
    resp = {
        "id": str(submission.id),
        "exam_problem_id": str(submission.exam_problem_id),
        "language": submission.language,
        "source_code": submission.source_code,
        "status": submission.status,
        "passed": submission.passed,
        "total": submission.total,
        "detail": submission.detail,
        "created_at": submission.created_at.isoformat(),
    }
    if attempt:
        resp["attempt"] = services.exam_attempt_payload(attempt)
    return JSONResponse(status_code=http_status, content=resp)


@router.post("/questions/{question_id}/data-problem/check")
def check_data_problem_question(
    question_id: uuid.UUID,
    data: DataSubmitIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Check a data problem question in an exam: runs the student's filled cells
    + the hidden checker_code through ml-runner and returns the verdict without
    persisting anything. Lets students verify before submitting the whole exam."""
    if (err := _require_access(user)) is not None:
        return err
    question = db.get(Question, question_id)
    if not question or question.type != "datalab":
        return _err("Not found.", 404)
    if question.exam and not _can_view_exam(question.exam, user):
        return _err("Not found.", 404)
    cells = question.data_problem_cells or []
    if not cells:
        return _err("This question has no data problem configured.", 400)
    if not any(a.source.strip() for a in data.answers):
        return _err("Fill in the code cell before checking.", 400)

    import requests as _requests

    answers_by_index = {a.index: a.source for a in data.answers}
    try:
        outcome = services.run_data_blocks(
            db,
            cells,
            answers_by_index,
            question.data_problem_checker_code or "",
            default_dataset=question.dataset,
        )
    except _requests.RequestException as exc:
        return _err(f"ml-runner request failed: {exc}", 502)

    return outcome


@router.post("/questions/{question_id}/check-block")
def check_data_problem_question_block(
    question_id: uuid.UUID,
    data: DataBlockCheckIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Per-block check for a data-problem question in an exam: runs cumulatively up
    to the given editable cell and runs that cell's check. Mirrors the standalone
    data problem's /check-block."""
    if (err := _require_access(user)) is not None:
        return err
    question = db.get(Question, question_id)
    if not question or question.type != "datalab":
        return _err("Not found.", 404)
    if question.exam and not _can_view_exam(question.exam, user):
        return _err("Not found.", 404)
    cells = question.data_problem_cells or []
    idx = data.index
    if (
        idx < 0
        or idx >= len(cells)
        or cells[idx].get("kind") != "code"
        or not cells[idx].get("editable")
    ):
        return _err("That block can't be checked.", 400)

    import requests as _requests

    answers_by_index = {a.index: a.source for a in data.answers}
    try:
        outcome = services.run_data_blocks(
            db, cells[: idx + 1], answers_by_index, default_dataset=question.dataset
        )
    except _requests.RequestException as exc:
        return _err(f"ml-runner request failed: {exc}", 502)

    block = next((c for c in outcome["cell_results"] if c["index"] == idx), None)
    return {
        "status": block["status"] if block else outcome["status"],
        "feedback": block["feedback"] if block else outcome["feedback"],
        "stdout": outcome["stdout"],
        "images": outcome["images"],
    }
