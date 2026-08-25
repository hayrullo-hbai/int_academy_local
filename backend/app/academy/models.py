import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import BaseModel

# Only the languages we support. Judge0 language IDs.
LANGUAGE_CHOICES = ["python2", "python3", "sql"]

JUDGE0_LANGUAGE_IDS = {
    "python2": 70,
    "python3": 71,
    "sql": 82,
}


class Problem(BaseModel):
    __tablename__ = "academy_problems"

    slug: Mapped[str] = mapped_column(String(220), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    difficulty: Mapped[str] = mapped_column(String(10), default="easy")
    language: Mapped[str] = mapped_column(String(10))
    starter_code: Mapped[str] = mapped_column(Text, default="")
    reference_solution: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(100), default="manual")
    dedup_key: Mapped[str] = mapped_column(String(64), default="", index=True)

    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    test_cases = relationship(
        "TestCase",
        back_populates="problem",
        cascade="all, delete-orphan",
        order_by="TestCase.created_at",
    )
    owner = relationship("User", foreign_keys=[owner_id])


class TestCase(BaseModel):
    __tablename__ = "academy_test_cases"

    problem_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("academy_problems.id", ondelete="CASCADE")
    )
    stdin: Mapped[str] = mapped_column(Text, default="")
    expected_output: Mapped[str] = mapped_column(Text)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)

    problem = relationship("Problem", back_populates="test_cases")


class Dataset(BaseModel):
    """An admin-uploaded CSV that Data Lab users can run code against."""

    __tablename__ = "academy_datasets"

    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(220), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    file_path: Mapped[str] = mapped_column(String(255))  # relative to MEDIA_ROOT

    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    uploader = relationship("User", foreign_keys=[uploaded_by_id])


class Notebook(BaseModel):
    """A saved, runnable notebook owned by a user — an ordered list of code and
    markdown cells run against datasets by slug (`load_dataset("...")`). Unlike a
    DataProblem it isn't graded; it's a personal/scratch workspace that a user can
    save, reopen, and (for admins) promote into a graded data problem.

    cells: list of dicts, each {"kind": "markdown" | "code", "source": str}.
    """

    __tablename__ = "academy_notebooks"

    slug: Mapped[str] = mapped_column(String(220), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200), default="Untitled notebook")
    cells: Mapped[list] = mapped_column(JSONB, default=list)
    # Private by default; an author publishes it to make it solvable by learners.
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)

    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    owner = relationship("User", foreign_keys=[owner_id])


class NotebookSubmission(BaseModel):
    """A learner's attempt at a notebook. Each graded code cell (one carrying a
    solution) is auto-checked by comparing the learner's output to the solution's
    output; the submission stores the total score and per-cell results."""

    __tablename__ = "academy_notebook_submissions"

    notebook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_notebooks.id", ondelete="CASCADE"),
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # The learner's filled code per cell index: {"0": "...", "2": "..."}.
    answers: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # passed | failed | error
    score: Mapped[int] = mapped_column(Integer, default=0)
    max_score: Mapped[int] = mapped_column(Integer, default=0)
    # [{index, status, feedback, points, points_earned}]
    cell_results: Mapped[list] = mapped_column(JSONB, default=list)

    notebook = relationship("Notebook")
    user = relationship("User", foreign_keys=[user_id])


class DataProblem(BaseModel):
    """A Colab-style notebook task graded by ml-runner instead of Judge0.

    The examiner authors an ordered list of `cells` (see below). Most cells are
    pre-written by the examiner (markdown instructions or already-working code);
    one or more code cells are marked editable — blanks the student fills in.
    On submit, every code cell (examiner's + the student's fills) is concatenated
    top-to-bottom into one program and run once, then the hidden `checker_code`
    runs in the same namespace and asserts/raises on failure.

    cells: list of dicts, each:
        {
          "kind": "markdown" | "code",
          "source": str,     # markdown text, or code. For an editable code cell
                             # this holds the examiner's REFERENCE answer (hidden).
          "editable": bool,  # code cells only — the student fills this one in
          "example": str,    # editable cells only — the hint/example shown to the
                             # student and pre-filled into their editor
          "points": int,     # editable cells only — worth of this block; a
                             # submission earns it if the block passes (partial credit)
        }
    Neither the reference `source` of editable cells nor `checker_code` is ever
    returned to students.
    """

    __tablename__ = "academy_data_problems"

    slug: Mapped[str] = mapped_column(String(220), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    difficulty: Mapped[str] = mapped_column(String(10), default="easy")
    cells: Mapped[list] = mapped_column(JSONB, default=list)
    checker_code: Mapped[str] = mapped_column(Text, default="")  # hidden
    source: Mapped[str] = mapped_column(String(100), default="manual")

    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_datasets.id", ondelete="SET NULL"),
        nullable=True,
    )
    dataset = relationship("Dataset")
    owner = relationship("User", foreign_keys=[owner_id])


class DataSubmission(BaseModel):
    __tablename__ = "academy_data_submissions"

    data_problem_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("academy_data_problems.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_code: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # passed | failed | error
    stdout: Mapped[str] = mapped_column(Text, default="")
    feedback: Mapped[str] = mapped_column(Text, default="")
    images: Mapped[list] = mapped_column(JSONB, default=list)
    # Partial-credit result: sum of points from blocks that passed, out of the
    # sum of every editable block's authored points.
    score: Mapped[int] = mapped_column(Integer, default=0)
    max_score: Mapped[int] = mapped_column(Integer, default=0)
    # Per-editable-block grading: [{index, status: passed|failed|error, feedback, points, max_points}]
    cell_results: Mapped[list] = mapped_column(JSONB, default=list)
    # Free-text answers to `text` cells, keyed by cell index (not auto-graded).
    text_answers: Mapped[dict] = mapped_column(JSONB, default=dict)

    data_problem = relationship("DataProblem")


class Submission(BaseModel):
    __tablename__ = "academy_submissions"

    problem_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("academy_problems.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    language: Mapped[str] = mapped_column(String(10))
    source_code: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    passed: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[list] = mapped_column(JSONB, default=list)

    problem = relationship("Problem")


class Exam(BaseModel):
    __tablename__ = "academy_exams"

    title: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(220), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    access_type: Mapped[str] = mapped_column(
        String(10), default="public"
    )  # public | private

    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    questions = relationship(
        "Question",
        back_populates="exam",
        cascade="all, delete-orphan",
        order_by="Question.order",
    )
    owner = relationship("User", foreign_keys=[owner_id])
    invites = relationship(
        "ExamInvite", back_populates="exam", cascade="all, delete-orphan"
    )


class ExamInvite(BaseModel):
    """An email invited to a private exam."""

    __tablename__ = "academy_exam_invites"
    __table_args__ = (UniqueConstraint("exam_id", "email", name="uq_exam_invite"),)

    exam_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_exams.id", ondelete="CASCADE"),
        index=True,
    )
    email: Mapped[str] = mapped_column(String(255))

    exam = relationship("Exam", back_populates="invites")


class ExamAttempt(BaseModel):
    """A user's saved attempt at an exam. Multiple submissions are allowed —
    each creates a new row identified by ``submitted_at``.

    ``submitted=False`` rows are draft attempts used for live per-question
    grading (Check/Run while taking).  Once the student clicks "Submit exam"
    a new row with ``submitted=True`` is appended; prior drafts are preserved.

    ``answers`` is a per-question breakdown, one dict per exam question:
        {question_id, order, type, title, points, answer, points_earned,
         graded: bool, detail}
    MCQ and code questions are auto-graded; text/datalab are stored ungraded
    (graded=False, points_earned=0) since they carry no auto-checker.
    """

    __tablename__ = "academy_exam_attempts"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    exam_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_exams.id", ondelete="CASCADE"),
        index=True,
    )
    score: Mapped[int] = mapped_column(Integer, default=0)
    max_score: Mapped[int] = mapped_column(Integer, default=0)
    answers: Mapped[list] = mapped_column(JSONB, default=list)
    submitted: Mapped[bool] = mapped_column(Boolean, default=False)
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    exam = relationship("Exam")


class Question(BaseModel):
    __tablename__ = "academy_questions"

    exam_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("academy_exams.id", ondelete="CASCADE")
    )
    order: Mapped[int] = mapped_column(Integer, default=0)
    type: Mapped[str] = mapped_column(
        String(10), default="mcq"
    )  # mcq | text | datalab | code
    title: Mapped[str] = mapped_column(String(200), default="")
    body: Mapped[list] = mapped_column(JSONB, default=list)
    points: Mapped[int] = mapped_column(Integer, default=1)

    # MCQ: [{"text": "...", "correct": true/false}]
    choices: Mapped[list] = mapped_column(JSONB, default=list)
    multiple: Mapped[bool] = mapped_column(Boolean, default=False)

    # Data Lab
    dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_datasets.id", ondelete="SET NULL"),
        nullable=True,
    )
    starter_code: Mapped[str] = mapped_column(Text, default="")

    # Data problem (copied from shared bank for exam self-containment)
    data_problem_cells: Mapped[list] = mapped_column(JSONB, default=list)
    data_problem_checker_code: Mapped[str] = mapped_column(Text, default="")
    data_problem_slug: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Coding problem — copy into ExamProblem on write (never linked directly)
    problem_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_problems.id", ondelete="SET NULL"),
        nullable=True,
    )

    exam = relationship("Exam", back_populates="questions")
    dataset = relationship("Dataset")
    problem = relationship("Problem")
    # …or author a problem that only exists for this exam question (see ExamProblem).
    exam_problem = relationship(
        "ExamProblem",
        back_populates="question",
        uselist=False,
        cascade="all, delete-orphan",
    )


class ExamProblem(BaseModel):
    """A coding problem authored inline for one exam question.

    Deliberately separate from Problem/academy_problems: it never appears in the
    shared /academy/problems bank, is owned 1:1 by its Question, and is deleted
    with it.
    """

    __tablename__ = "academy_exam_problems"

    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_questions.id", ondelete="CASCADE"),
        unique=True,
    )
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    difficulty: Mapped[str] = mapped_column(String(10), default="easy")
    language: Mapped[str] = mapped_column(String(10))
    starter_code: Mapped[str] = mapped_column(Text, default="")
    reference_solution: Mapped[str] = mapped_column(Text, default="")
    problem_slug: Mapped[str | None] = mapped_column(String(100), nullable=True)

    question = relationship("Question", back_populates="exam_problem")
    test_cases = relationship(
        "ExamProblemTestCase",
        back_populates="exam_problem",
        cascade="all, delete-orphan",
        order_by="ExamProblemTestCase.created_at",
    )


class ExamProblemTestCase(BaseModel):
    __tablename__ = "academy_exam_problem_test_cases"

    exam_problem_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("academy_exam_problems.id", ondelete="CASCADE")
    )
    stdin: Mapped[str] = mapped_column(Text, default="")
    expected_output: Mapped[str] = mapped_column(Text)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False)

    exam_problem = relationship("ExamProblem", back_populates="test_cases")


class ExamSubmission(BaseModel):
    """Submissions against an ExamProblem — kept separate from academy_submissions
    (the shared problem bank's submission history)."""

    __tablename__ = "academy_exam_submissions"

    exam_problem_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("academy_exam_problems.id", ondelete="CASCADE")
    )
    language: Mapped[str] = mapped_column(String(10))
    source_code: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    passed: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[list] = mapped_column(JSONB, default=list)

    exam_problem = relationship("ExamProblem")
