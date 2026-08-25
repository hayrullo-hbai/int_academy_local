import re
import uuid

from pydantic import BaseModel, Field, field_validator

from app.academy.models import LANGUAGE_CHOICES

# Letters, dash, underscore only — no digits, spaces, or other punctuation.
SLUG_RE = re.compile(r"^[a-z0-9_-]+$")

DIFFICULTY_CHOICES = ["easy", "medium", "hard"]


def _validate_slug(value: str) -> str:
    if not SLUG_RE.match(value):
        raise ValueError("Slug may only contain letters, dashes, and underscores.")
    return value


def _non_empty(label: str):
    """Reject empty / whitespace-only required text fields."""

    def _validate(value: str) -> str:
        if value is None or not str(value).strip():
            raise ValueError(f"{label} is required.")
        return value

    return _validate


def _validate_language(value: str) -> str:
    if value not in LANGUAGE_CHOICES:
        raise ValueError(f"Language must be one of: {', '.join(LANGUAGE_CHOICES)}.")
    return value


def _validate_difficulty(value: str) -> str:
    if value not in DIFFICULTY_CHOICES:
        raise ValueError(f"Difficulty must be one of: {', '.join(DIFFICULTY_CHOICES)}.")
    return value


# Every authored coding problem — in the shared bank or embedded in an exam —
# needs at least this many test cases (with at least one marked as a sample).
MIN_TEST_CASES = 5


def _validate_test_cases(value: list) -> list:
    if len(value) < MIN_TEST_CASES:
        raise ValueError(f"Add at least {MIN_TEST_CASES} test cases.")
    total = len(value)
    sample_count = sum(1 for tc in value if tc.is_sample)
    if sample_count == 0:
        raise ValueError(
            "Mark at least one test case as a sample so solvers can see an example."
        )
    # At least half must stay hidden, i.e. samples may be at most half (rounded down).
    if sample_count > total // 2:
        raise ValueError(
            "At least 50% of test cases must be hidden — reduce the number marked as sample."
        )
    return value


# ---------- problems ----------
class TestCaseIn(BaseModel):
    stdin: str = ""
    expected_output: str = ""
    is_sample: bool = False

    _validate_expected_output = field_validator("expected_output")(
        _non_empty("Expected output")
    )


class TestCaseOut(BaseModel):
    id: uuid.UUID
    stdin: str
    expected_output: str
    is_sample: bool

    class Config:
        from_attributes = True


class ProblemListOut(BaseModel):
    id: uuid.UUID
    slug: str
    title: str
    difficulty: str
    language: str
    is_owner: bool = False

    class Config:
        from_attributes = True


class ProblemDetailOut(BaseModel):
    id: uuid.UUID
    slug: str
    title: str
    description: str
    difficulty: str
    language: str
    starter_code: str
    reference_solution: str = ""  # only populated for the owner / academy admins
    sample_test_cases: list[TestCaseOut]
    test_cases: list[TestCaseOut] = Field(default_factory=list)
    is_owner: bool = False

    class Config:
        from_attributes = True


class ProblemCreateIn(BaseModel):
    slug: str = ""  # auto-generated from the title on the server
    title: str
    description: str
    difficulty: str = "easy"
    language: str
    starter_code: str = ""
    reference_solution: str
    test_cases: list[TestCaseIn]

    _validate_title = field_validator("title")(_non_empty("Title"))
    _validate_description = field_validator("description")(_non_empty("Description"))
    _validate_language = field_validator("language")(_validate_language)
    _validate_difficulty = field_validator("difficulty")(_validate_difficulty)
    _validate_reference = field_validator("reference_solution")(
        _non_empty("Reference solution")
    )
    _validate_test_cases = field_validator("test_cases")(_validate_test_cases)


class ProblemUpdateIn(BaseModel):
    title: str
    description: str
    difficulty: str = "easy"
    language: str
    starter_code: str = ""
    reference_solution: str
    test_cases: list[TestCaseIn]

    _validate_title = field_validator("title")(_non_empty("Title"))
    _validate_description = field_validator("description")(_non_empty("Description"))
    _validate_language = field_validator("language")(_validate_language)
    _validate_difficulty = field_validator("difficulty")(_validate_difficulty)
    _validate_reference = field_validator("reference_solution")(
        _non_empty("Reference solution")
    )
    _validate_test_cases = field_validator("test_cases")(_validate_test_cases)


class SubmitIn(BaseModel):
    source_code: str
    language: str | None = None


class SubmissionOut(BaseModel):
    id: uuid.UUID
    problem_id: uuid.UUID
    language: str
    source_code: str
    status: str
    passed: int
    total: int
    detail: list
    created_at: object

    class Config:
        from_attributes = True


class RunIn(BaseModel):
    language: str
    source_code: str
    stdin: str = ""


# ---------- datasets ----------
class DatasetOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str
    columns: list[str] = Field(default_factory=list)

    class Config:
        from_attributes = True


class MLRunIn(BaseModel):
    code: str
    dataset: str = ""


# ---------- notebooks (saved, runnable; code cells may be auto-graded) ----------
class NotebookCell(BaseModel):
    kind: str = "code"  # "code" | "markdown"
    source: str = ""
    solution: str = ""  # code cells only — reference answer for auto-checking
    points: int = 0  # code cells only — awarded when the cell's output matches

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        return v if v in ("code", "markdown") else "code"

    @field_validator("points")
    @classmethod
    def _points(cls, v: int) -> int:
        return max(0, v)


class NotebookIn(BaseModel):
    slug: str = ""  # auto-generated from the title on the server
    title: str = "Untitled notebook"
    cells: list[NotebookCell] = Field(default_factory=list)
    is_public: bool = False


class NotebookUpdateIn(BaseModel):
    title: str = "Untitled notebook"
    cells: list[NotebookCell] = Field(default_factory=list)
    is_public: bool = False


class NotebookListOut(BaseModel):
    id: uuid.UUID
    slug: str
    title: str
    is_owner: bool = False
    is_public: bool = False

    class Config:
        from_attributes = True


class NotebookOut(BaseModel):
    id: uuid.UUID
    slug: str
    title: str
    cells: list[NotebookCell] = Field(default_factory=list)
    is_owner: bool = False
    can_author: bool = False  # true for academy admins / the owner (see solutions)
    is_public: bool = False


class NotebookRunCellIn(BaseModel):
    """Run a notebook up to (and including) the code cell at `index`, returning
    that cell's output. Markdown cells are skipped; code cells 0..index run in one
    shared namespace (ml-runner is stateless, so earlier cells re-run each time)."""

    cells: list[NotebookCell] = Field(default_factory=list)
    index: int
    dataset: str | None = None  # optional default dataset, bound to `df`


class NotebookAnswerIn(BaseModel):
    index: int
    source: str = ""


class NotebookSubmitIn(BaseModel):
    answers: list[NotebookAnswerIn] = Field(default_factory=list)


# ---------- data problems (Colab-style notebook, graded by ml-runner) ----------
def _validate_cells(value: list) -> list:
    """A notebook needs at least one editable code cell for the student to fill,
    and every editable cell needs a reference answer so it can be validated."""
    code_cells = [c for c in value if c.kind == "code"]
    editable = [c for c in code_cells if c.editable]
    if not code_cells:
        raise ValueError("Add at least one code cell.")
    if not editable:
        raise ValueError(
            "Mark at least one code cell as editable (the blank the student fills in)."
        )
    for i, c in enumerate(editable):
        if not c.source.strip():
            raise ValueError(
                "Every editable cell needs a reference answer in its code so it can be validated."
            )
    return value


class CellIn(BaseModel):
    kind: str = "code"  # "markdown" | "code" | "text"
    source: str = (
        ""  # markdown text, code (reference answer), or a text question's prompt
    )
    editable: bool = False
    example: str = ""  # hint pre-filled into the student's editor (editable cells)
    check: str = ""  # per-block assertions run right after this cell (editable cells)
    points: int = 1  # worth of this block, editable cells only (partial credit)

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in ("markdown", "code", "text"):
            raise ValueError("Cell kind must be 'markdown', 'code', or 'text'.")
        return v

    @field_validator("points")
    @classmethod
    def _points(cls, v: int) -> int:
        if v < 0:
            raise ValueError("Points can't be negative.")
        return v


class CellOut(BaseModel):
    """A cell as shown to the student — editable code cells never leak their
    reference `source`; the student gets the `example` instead."""

    kind: str
    source: str
    editable: bool
    example: str = ""
    points: int = 0


class DataProblemListOut(BaseModel):
    id: uuid.UUID
    slug: str
    title: str
    difficulty: str
    dataset: str | None = None
    is_owner: bool = False

    class Config:
        from_attributes = True


class DataProblemDetailOut(BaseModel):
    id: uuid.UUID
    slug: str
    title: str
    description: str
    difficulty: str
    cells: list[CellOut]
    dataset: str | None = None
    dataset_columns: list[str] = Field(default_factory=list)
    checker_code: str = ""
    is_owner: bool = False


class DataProblemCreateIn(BaseModel):
    slug: str
    title: str
    description: str = ""
    difficulty: str = "easy"
    cells: list[CellIn]
    checker_code: str = (
        ""  # optional global checker, run as a final block after all cells
    )
    dataset_slug: str | None = None

    _validate_slug = field_validator("slug")(_validate_slug)
    _validate_title = field_validator("title")(_non_empty("Title"))
    _validate_difficulty = field_validator("difficulty")(_validate_difficulty)
    _validate_cells = field_validator("cells")(_validate_cells)


class DataProblemUpdateIn(BaseModel):
    title: str
    description: str = ""
    difficulty: str = "easy"
    cells: list[CellIn]
    checker_code: str = (
        ""  # optional global checker, run as a final block after all cells
    )
    dataset_slug: str | None = None

    _validate_title = field_validator("title")(_non_empty("Title"))
    _validate_difficulty = field_validator("difficulty")(_validate_difficulty)
    _validate_cells = field_validator("cells")(_validate_cells)


class DataAnswerIn(BaseModel):
    index: int  # position of the editable cell in the notebook's cell list
    source: str = ""


class DataSubmitIn(BaseModel):
    answers: list[DataAnswerIn] = Field(default_factory=list)


class DataBlockCheckIn(BaseModel):
    answers: list[DataAnswerIn] = Field(default_factory=list)
    index: int  # the editable cell to run cumulatively up to, and check


class DataSubmissionOut(BaseModel):
    id: uuid.UUID
    data_problem_id: uuid.UUID
    source_code: str
    status: str
    stdout: str
    feedback: str
    images: list
    score: int = 0
    max_score: int = 0
    cell_results: list = Field(default_factory=list)
    text_answers: dict = Field(default_factory=dict)
    created_at: object

    class Config:
        from_attributes = True


# ---------- exams ----------
class ExamProblemOut(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    difficulty: str
    language: str
    starter_code: str
    sample_test_cases: list[TestCaseOut]
    problem_slug: str | None = None


class DataProblemCellOut(BaseModel):
    kind: str
    source: str
    editable: bool
    example: str = ""
    points: int = 0


class ExamSubmitIn(BaseModel):
    source_code: str
    language: str | None = None


class ExamSubmissionOut(BaseModel):
    id: uuid.UUID
    exam_problem_id: uuid.UUID
    language: str
    source_code: str
    status: str
    passed: int
    total: int
    detail: list
    created_at: object

    class Config:
        from_attributes = True


class QuestionWriteIn(BaseModel):
    order: int = 0
    type: str = "mcq"
    title: str = ""
    body: list = Field(default_factory=list)
    points: int = 1
    choices: list = Field(default_factory=list)
    multiple: bool = False
    starter_code: str = ""
    dataset_slug: str | None = None
    problem_slug: str | None = None
    data_problem_slug: str | None = None


class ExamWriteIn(BaseModel):
    title: str
    slug: str = ""  # auto-generated from the title on the server
    description: str = ""
    is_published: bool = False
    access_type: str = "public"
    questions: list[QuestionWriteIn] = Field(default_factory=list)


class QuestionReadOut(BaseModel):
    id: uuid.UUID
    order: int
    type: str
    title: str
    body: list
    points: int
    choices: list  # `correct` flag stripped before returning
    multiple: bool
    starter_code: str
    dataset: str | None = None
    problem: str | None = None
    exam_problem: ExamProblemOut | None = None
    data_problem_cells: list[DataProblemCellOut] = Field(default_factory=list)
    data_problem_dataset: str | None = None
    problem_slug: str | None = None
    data_problem_slug: str | None = None


class ExamListOut(BaseModel):
    id: uuid.UUID
    slug: str
    title: str
    description: str
    is_published: bool
    access_type: str = "public"
    question_count: int
    is_owner: bool = False
    invited_emails: list[str] = Field(default_factory=list)


class ExamReadOut(BaseModel):
    id: uuid.UUID
    slug: str
    title: str
    description: str
    is_published: bool
    access_type: str = "public"
    questions: list[QuestionReadOut]
    is_owner: bool = False
    invited_emails: list[str] = Field(default_factory=list)


class ExamInviteIn(BaseModel):
    email: str


class UserSearchResult(BaseModel):
    id: str
    email: str
    full_name: str | None = None


class AnswerIn(BaseModel):
    selected: list[int] = Field(default_factory=list)


class ExamAnswerIn(BaseModel):
    """One question's answer inside a whole-exam submission. Only the field
    relevant to the question's type is used (selected for mcq, source_code for
    code, text for text; datalab uses source_code plus per-cell `cells` so each
    editable cell can be graded for its share of the points)."""

    question_id: uuid.UUID
    selected: list[int] = Field(default_factory=list)
    text: str = ""
    source_code: str = ""
    language: str | None = None
    # datalab only — the student's fill for each editable cell, {index, source}.
    cells: list[DataAnswerIn] = Field(default_factory=list)


class ExamAttemptSubmitIn(BaseModel):
    answers: list[ExamAnswerIn] = Field(default_factory=list)


class ExamAttemptOut(BaseModel):
    id: uuid.UUID
    exam_slug: str
    exam_title: str
    score: int
    max_score: int
    percentage: float = 0.0
    submitted: bool = False
    answers: list  # per-question breakdown (see services.submit_exam_attempt)
    submitted_at: object

    class Config:
        from_attributes = True


class VisibilityIn(BaseModel):
    public: bool
