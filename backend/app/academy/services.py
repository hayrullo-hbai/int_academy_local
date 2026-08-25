import uuid
from datetime import datetime, timezone

import base64
import csv
import io
import random
import re
import string
from types import SimpleNamespace
from typing import TYPE_CHECKING

import requests
from sqlalchemy.orm import Session

from app.academy import judge0, mlrunner
from app.academy.models import (
    DataProblem,
    DataSubmission,
    Dataset,
    Notebook,
    NotebookSubmission,
    Exam,
    ExamAttempt,
    ExamProblem,
    ExamProblemTestCase,
    ExamSubmission,
    Problem,
    Question,
    Submission,
    TestCase,
)

from app.core.config import settings

if TYPE_CHECKING:  # annotation only — avoids a schemas <-> services import cycle
    from app.academy.schemas import QuestionWriteIn


class AcademyError(Exception):
    pass


# ---------- problems / submissions ----------
def verify_reference_solution(language: str, reference_solution: str, test_cases):
    """Run the reference solution against every test case via Judge0 before an
    admin uploads a problem, so we never save a problem whose own solution fails
    its test cases.

    `test_cases` may be pydantic TestCaseIn objects (no `.id` yet) — wrap them so
    judge0.run_against_testcases can build its detail list.

    Returns None if the solution passes all cases, otherwise (message, status_code).
    """
    if not reference_solution or not reference_solution.strip():
        return ("Provide a reference solution so the problem can be validated.", 400)
    if not test_cases:
        return ("Add at least one test case.", 400)

    shim = [
        SimpleNamespace(
            id=i + 1,
            stdin=tc.stdin,
            expected_output=tc.expected_output,
            is_sample=tc.is_sample,
        )
        for i, tc in enumerate(test_cases)
    ]
    try:
        passed, total, detail = judge0.run_against_testcases(
            language, reference_solution, shim
        )
    except requests.RequestException as exc:
        return (f"Could not reach Judge0 to validate the problem: {exc}", 502)

    if passed == total:
        return None

    reasons = []
    for i, d in enumerate(detail):
        if d["passed"]:
            continue
        reasons.append(f"Test case #{i + 1}: {_explain_failure(d)}")
    return (
        f"Your reference solution doesn't pass its own test cases "
        f"({passed}/{total} passed). Fix the solution or the expected outputs, "
        f"then upload again.\n" + "\n".join(reasons),
        400,
    )


def _explain_failure(d: dict) -> str:
    """A one-line, human reason a test case failed — no raw tracebacks."""
    status = (d.get("status") or "").lower()
    stderr = (d.get("stderr") or "").strip()

    if "compilation" in status:
        return "the code has a syntax error and couldn't compile."
    if "time limit" in status:
        return "the code ran too long (time limit exceeded)."
    if "runtime error" in status or (stderr and "error" in status):
        # Show only the exception's final line, not the whole traceback.
        last = stderr.splitlines()[-1] if stderr else ""
        return (
            f"the code crashed while running — {last}"
            if last
            else "the code crashed while running."
        )

    # Wrong answer — show expected vs actual when it's a visible (sample) case.
    expected, got = d.get("expected"), d.get("stdout")
    if expected is not None and got is not None:
        return f"expected output {expected!r} but the solution printed {got!r}."
    return "the solution's output didn't match the expected output."


def create_problem(db: Session, data, owner_id: uuid.UUID | None = None) -> Problem:
    problem = Problem(
        slug=data.slug,
        title=data.title,
        description=data.description,
        difficulty=data.difficulty,
        language=data.language,
        starter_code=data.starter_code,
        reference_solution=data.reference_solution,
        owner_id=owner_id,
    )
    db.add(problem)
    db.flush()
    for tc in data.test_cases:
        db.add(
            TestCase(
                problem_id=problem.id,
                stdin=tc.stdin,
                expected_output=tc.expected_output,
                is_sample=tc.is_sample,
            )
        )
    db.commit()
    db.refresh(problem)
    return problem


def update_problem(db: Session, problem: Problem, data) -> Problem:
    problem.title = data.title
    problem.description = data.description
    problem.difficulty = data.difficulty
    problem.language = data.language
    problem.starter_code = data.starter_code
    problem.reference_solution = data.reference_solution
    for tc in list(problem.test_cases):
        db.delete(tc)
    db.flush()
    for tc in data.test_cases:
        db.add(
            TestCase(
                problem_id=problem.id,
                stdin=tc.stdin,
                expected_output=tc.expected_output,
                is_sample=tc.is_sample,
            )
        )
    db.commit()
    db.refresh(problem)
    return problem


def sample_test_cases(problem: Problem):
    return [tc for tc in problem.test_cases if tc.is_sample]


def _grade(
    db: Session, submission, test_cases: list, language: str, source_code: str
) -> int:
    """Run test_cases via Judge0, fill in `submission`'s result fields, commit.

    Shared by Submission (problem bank) and ExamSubmission (exam-only problems) —
    both rows shape (status/passed/total/detail) identically. Returns http_status.
    """
    if not test_cases:
        submission.status = "error"
        submission.detail = [{"error": "Problem has no test cases."}]
        db.commit()
        db.refresh(submission)
        return 400

    try:
        passed, total, detail = judge0.run_against_testcases(
            language, source_code, test_cases
        )
    except requests.RequestException as exc:
        submission.status = "error"
        submission.detail = [{"error": f"Judge0 request failed: {exc}"}]
        db.commit()
        db.refresh(submission)
        return 502

    submission.passed = passed
    submission.total = total
    submission.detail = detail

    status_ids = {d.get("status_id") for d in detail}
    if passed == total:
        submission.status = "accepted"
    elif status_ids & ({6} | set(range(7, 15))):
        submission.status = "error"
    else:
        submission.status = "wrong_answer"

    db.commit()
    db.refresh(submission)
    return 200


def submit_solution(
    db: Session, problem: Problem, source_code: str, language: str | None, user=None
) -> tuple[Submission, int]:
    """Grade a submission against Judge0. Returns (submission, http_status)."""
    language = language or problem.language
    submission = Submission(
        problem_id=problem.id,
        language=language,
        source_code=source_code,
        user_id=user.id if user else None,
    )
    db.add(submission)
    db.flush()
    http_status = _grade(
        db, submission, list(problem.test_cases), language, source_code
    )
    return submission, http_status


def submit_exam_problem(
    db: Session, exam_problem: ExamProblem, source_code: str, language: str | None
) -> tuple[ExamSubmission, int]:
    """Grade a submission against an exam-only problem. Returns (submission, http_status)."""
    language = language or exam_problem.language
    submission = ExamSubmission(
        exam_problem_id=exam_problem.id, language=language, source_code=source_code
    )
    db.add(submission)
    db.flush()
    http_status = _grade(
        db, submission, list(exam_problem.test_cases), language, source_code
    )
    return submission, http_status


# ---------- datasets ----------
def dataset_columns(dataset: Dataset) -> list[str]:
    path = settings.MEDIA_ROOT / dataset.file_path
    try:
        with open(path, newline="") as fh:
            text = fh.read(4096)
        reader = csv.reader(io.StringIO(text))
        return next(reader, [])
    except OSError:
        return []


# ---------- data problems (ml-runner + per-block checks) ----------
# A data problem is graded block-by-block: each code cell runs in order sharing
# one namespace; right after an editable cell we run its `check` assertions. Each
# block reports passed / failed (check assertion raised) / error (the cell's own
# code raised). Markers below delimit each block's output in the single ml-runner
# run. Control chars make them practically impossible for taker code to forge.
_BLK = "\x01BLK"  # f"{_BLK}:{idx}:START\x01" / ":PASS\x01" / ":FAIL\x01" / ":ERROR\x01"
_FINAL_IDX = -1  # the optional global checker_code runs as a final block

# Injected once at the top of every graded run. `_blk` executes a cell's code and
# (for editable cells) its check in the shared module namespace, catching failures
# so grading always continues to the next block.
_BLOCK_RUNNER = (
    "import traceback as _tb, sys as _sys, ast as _ast\n"
    "def _run_src(_code):\n"
    "    # Execute a cell, and echo a trailing bare expression like a notebook.\n"
    "    _tree = _ast.parse(_code)\n"
    "    if _tree.body and isinstance(_tree.body[-1], _ast.Expr):\n"
    "        _last = _ast.Expression(_tree.body.pop().value)\n"
    "        exec(compile(_tree, '<cell>', 'exec'), globals())\n"
    "        _val = eval(compile(_last, '<cell>', 'eval'), globals())\n"
    "        if _val is not None:\n"
    "            print(repr(_val))\n"
    "    else:\n"
    "        exec(compile(_tree, '<cell>', 'exec'), globals())\n"
    "def _blk(_idx, _code, _check):\n"
    "    print(f'\\x01BLK:{_idx}:START\\x01')\n"
    "    try:\n"
    "        _run_src(_code)\n"
    "    except Exception:\n"
    "        _tb.print_exc(file=_sys.stdout)\n"
    "        print(f'\\x01BLK:{_idx}:ERROR\\x01')\n"
    "        return\n"
    "    if _check.strip():\n"
    "        try:\n"
    "            exec(_check, globals())\n"
    "        except Exception:\n"
    "            _tb.print_exc(file=_sys.stdout)\n"
    "            print(f'\\x01BLK:{_idx}:FAIL\\x01')\n"
    "            return\n"
    "    print(f'\\x01BLK:{_idx}:PASS\\x01')\n"
)


def slugify(text: str) -> str:
    """Lowercase URL slug from any title: letters + digits kept, runs of anything
    else become single dashes. Numbers are allowed, so numeric titles are fine."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s or "item"


def unique_slug(db: Session, model, title: str, exclude_id=None) -> str:
    """Auto-make a unique slug from `title`. If the base is taken, append a random
    lowercase letter and keep appending until it's free (skips `exclude_id`, so a
    row can keep its own slug on update)."""
    base = slugify(title)
    slug = base
    while True:
        q = db.query(model).filter(model.slug == slug)
        if exclude_id is not None:
            q = q.filter(model.id != exclude_id)
        if not db.query(q.exists()).scalar():
            return slug
        slug += random.choice(string.ascii_lowercase)


def _dataset_for(db: Session, dataset_slug: str | None):
    if not dataset_slug:
        return None
    return db.query(Dataset).filter(Dataset.slug == dataset_slug).first()


def dataset_preamble(dataset: "Dataset | None") -> str:
    """Python that reconstructs `df` from the dataset, embedded inline so it works
    no matter where ml-runner runs (it needs no access to our media files). The
    CSV is base64-encoded to survive any quoting/newlines in the data.

    Returns "" when there's no dataset (df is simply never defined)."""
    if not dataset:
        return ""
    path = settings.MEDIA_ROOT / dataset.file_path
    with open(path, "rb") as fh:
        payload = base64.b64encode(fh.read()).decode()
    return (
        "import base64 as _b64, io as _io\n"
        "import pandas as _pd\n"
        f'df = _pd.read_csv(_io.BytesIO(_b64.b64decode("{payload}")))\n'
    )


# Matches load_dataset("slug") / load_dataset('slug') anywhere in a cell's code.
_LOAD_DATASET_RE = re.compile(r"""load_dataset\(\s*["']([A-Za-z0-9_-]+)["']\s*\)""")


def _dataset_b64(dataset: "Dataset") -> str:
    with open(settings.MEDIA_ROOT / dataset.file_path, "rb") as fh:
        return base64.b64encode(fh.read()).decode()


def build_dataset_loader(
    db: Session, code: str, default_dataset: "Dataset | None" = None
) -> str:
    """Preamble that lets a notebook pull any dataset by slug:
    `df = load_dataset("titanic")`.

    ml-runner has no DB or filesystem access, so every dataset referenced in the
    code via `load_dataset("slug")` (plus an optional `default_dataset`) is
    base64-embedded here and rebuilt in-process. `default_dataset`, when given, is
    also bound to `df` so single-dataset problems keep working unchanged.
    """
    slugs = set(_LOAD_DATASET_RE.findall(code or ""))
    if default_dataset:
        slugs.add(default_dataset.slug)
    embedded: dict[str, str] = {}
    for slug in sorted(slugs):
        ds = _dataset_for(db, slug)
        if ds:
            embedded[slug] = _dataset_b64(ds)

    lines = [
        "import base64 as _b64, io as _io",
        "import pandas as _pd",
        "_DATASETS = {",
    ]
    for slug, payload in embedded.items():
        lines.append(f"    {slug!r}: {payload!r},")
    lines.append("}")
    lines += [
        "def load_dataset(_slug):",
        "    if _slug not in _DATASETS:",
        "        raise ValueError(",
        '            f"Dataset {_slug!r} is not available. Reference an existing '
        'dataset by its slug; "',
        '            f"available in this notebook: {sorted(_DATASETS)}.")',
        "    return _pd.read_csv(_io.BytesIO(_b64.b64decode(_DATASETS[_slug])))",
    ]
    if default_dataset and default_dataset.slug in embedded:
        lines.append(f"df = load_dataset({default_dataset.slug!r})")
    return "\n".join(lines) + "\n"


_EMPTY_BLOCK_CHECK = 'assert False, "This block is empty — write your answer."'


def echo_last_expression(code: str) -> str:
    """Make a plain script echo its final top-level expression like a notebook
    cell (e.g. `df.head()` prints the frame). Used by the ml-run endpoint so the
    exam/playground "Run" shows output the same way per-block grading does."""
    import ast

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code
    if not tree.body or not isinstance(tree.body[-1], ast.Expr):
        return code
    last = tree.body[-1]
    tree.body[-1] = ast.Assign(
        targets=[ast.Name(id="__disp", ctx=ast.Store())], value=last.value
    )
    tree.body.append(ast.parse("if __disp is not None: print(repr(__disp))").body[0])
    ast.fix_missing_locations(tree)
    try:
        return ast.unparse(tree)
    except Exception:
        return code


def _has_no_real_code(src: str) -> bool:
    """True when an answer is blank or only comments/whitespace — such a block
    must never count as passed, even for cells that carry no check."""
    import ast

    try:
        return len(ast.parse(src).body) == 0
    except SyntaxError:
        return False  # it has something (even if broken) — let it run and error


def _build_graded_program(
    preamble: str,
    cells: list,
    answers_by_index: dict[int, str],
    final_checker: str = "",
) -> tuple[str, list[int]]:
    """Assemble a single program that runs each code cell in order through `_blk`,
    so we get one pass/fail per block from one ml-runner execution. Returns the
    program text and the ordered list of editable cell indices that get graded."""
    parts = [preamble, _BLOCK_RUNNER]
    graded: list[int] = []
    for i, cell in enumerate(cells):
        if cell.get("kind") != "code":
            continue
        if cell.get("editable"):
            code = answers_by_index.get(i, "")
            check = cell.get("check", "")
            graded.append(i)
            if _has_no_real_code(code):
                # A blank / comment-only answer never passes, even with no check.
                parts.append(f"_blk({i}, '', {_EMPTY_BLOCK_CHECK!r})")
            else:
                parts.append(f"_blk({i}, {code!r}, {check!r})")
        else:
            # examiner-authored fixed cell: run it (no per-block check)
            parts.append(f"_blk({i}, {cell.get('source', '')!r}, '')")
    if final_checker.strip():
        parts.append(f"_blk({_FINAL_IDX}, '', {final_checker!r})")
    return "\n".join(parts), graded


def _parse_blocks(stdout: str) -> dict[int, dict]:
    """Split ml-runner stdout on the _blk markers into {index: {status, output}}."""
    import re

    marker = re.compile(r"\x01BLK:(-?\d+):(START|PASS|FAIL|ERROR)\x01")
    results: dict[int, dict] = {}
    cur_idx = None
    buf: list[str] = []
    pos = 0
    for m in marker.finditer(stdout):
        text = stdout[pos : m.start()]
        pos = m.end()
        idx, kind = int(m.group(1)), m.group(2)
        if kind == "START":
            cur_idx, buf = idx, []
        else:
            output = "".join(buf) + text
            status = {"PASS": "passed", "FAIL": "failed", "ERROR": "error"}[kind]
            results[idx] = {"status": status, "output": output.strip()}
            cur_idx = None
        if cur_idx is not None:
            buf.append(text)
    return results


def run_data_blocks(
    db: Session,
    cells: list,
    answers_by_index: dict[int, str],
    final_checker: str = "",
    default_dataset: "Dataset | None" = None,
) -> dict:
    """Grade a notebook block-by-block in one ml-runner run. Returns
    {status, stdout, feedback, images, cell_results} where cell_results is one
    entry per editable (graded) block: {index, status, feedback}.

    The dataset loader is derived from the program itself: any `load_dataset("slug")`
    in a cell (plus `default_dataset`, bound to `df`) is embedded automatically."""
    program, graded = _build_graded_program("", cells, answers_by_index, final_checker)
    preamble = build_dataset_loader(db, program, default_dataset)
    result = mlrunner.run(
        preamble + "\n" + program, ""
    )  # datasets embedded in preamble
    stdout = result.get("stdout") or ""
    run_error = (result.get("error") or "").strip()
    images = result.get("images") or []

    blocks = _parse_blocks(stdout)
    cell_results = []
    visible_out = []
    n_pass = 0
    for i in graded:
        blk = blocks.get(i, {"status": "error", "output": "This block did not run."})
        if blk["output"]:
            visible_out.append(blk["output"])
        cell_results.append(
            {"index": i, "status": blk["status"], "feedback": blk["output"]}
        )
        if blk["status"] == "passed":
            n_pass += 1

    final = blocks.get(_FINAL_IDX)
    final_ok = final["status"] == "passed" if final else True

    if run_error and not blocks:
        # The program crashed outside any block (bad preamble / examiner cell).
        status, feedback = "error", run_error
    elif n_pass == len(graded) and final_ok:
        status, feedback = "passed", f"All {len(graded)} blocks passed."
    else:
        status = "failed"
        feedback = f"{n_pass}/{len(graded)} blocks passed."
        if final and not final_ok:
            feedback += f"\nFinal check: {final['output']}"

    return {
        "status": status,
        "stdout": "\n".join(visible_out).rstrip("\n"),
        "feedback": feedback,
        "images": images,
        "cell_results": cell_results,
    }


def _assemble_notebook(cells: list, answers_by_index: dict[int, str]) -> str:
    """Concatenate every code cell top-to-bottom into one program. For editable
    cells use the student's answer (from answers_by_index); for the rest use the
    examiner's authored source. Markdown cells are skipped."""
    pieces = []
    for i, cell in enumerate(cells):
        if cell.get("kind") != "code":
            continue
        if cell.get("editable"):
            pieces.append(answers_by_index.get(i, ""))
        else:
            pieces.append(cell.get("source", ""))
    return "\n".join(pieces)


def student_cells(cells: list) -> list[dict]:
    """Strip reference answers before returning cells to a student.

    Legacy data problems: only `editable` code cells are blanks (source hidden,
    `example` shown). Notebook data problems: every code cell is a blank the
    student fills, and the `solution` is never included."""
    notebook = is_notebook_cells(cells)
    out = []
    for c in cells:
        kind = c.get("kind", "code")
        if notebook:
            # Every code cell is a fillable blank; markdown is shown as-is. The
            # author's `source` is the starter (shown as `example`); the hidden
            # `solution` is never included.
            editable = kind == "code"
            out.append(
                {
                    "kind": kind,
                    "source": "" if editable else c.get("source", ""),
                    "editable": editable,
                    "example": c.get("source", "") if editable else "",
                    "points": int(c.get("points", 0) or 0) if editable else 0,
                }
            )
        else:
            editable = bool(c.get("editable")) and kind == "code"
            out.append(
                {
                    "kind": kind,
                    "source": "" if editable else c.get("source", ""),
                    "editable": editable,
                    "example": c.get("example", "") if editable else "",
                    "points": int(c.get("points", 0) or 0) if editable else 0,
                }
            )
    return out


def _editable_points(cells: list) -> dict[int, int]:
    """Map each editable code cell's index to its authored points. Missing points
    default to 0 so cells authored before per-block points existed fall back to an
    even split of the exam question's points instead of silently becoming 1 each."""
    return {
        i: int(c.get("points", 0) or 0)
        for i, c in enumerate(cells)
        if bool(c.get("editable")) and c.get("kind") == "code"
    }


def verify_data_problem(
    db: Session, cells, checker_code: str, dataset_slug: str | None
):
    """Before saving, confirm the examiner's own filled-in notebook passes the
    checker — mirrors the Judge0 pre-check for coding problems. `cells` is a list
    of CellIn. Returns None on success, else (message, status_code)."""
    if not settings.MLRUNNER_URL:
        return (
            "ml-runner is not configured (MLRUNNER_URL is empty), so the "
            "data problem can't be validated. Set MLRUNNER_URL and try again.",
            503,
        )
    dataset = _dataset_for(db, dataset_slug)
    if dataset_slug and not dataset:
        return (f"Dataset '{dataset_slug}' was not found.", 400)

    cell_dicts = [c.model_dump() for c in cells]
    ref_answers = {
        i: c.get("source", "") for i, c in enumerate(cell_dicts) if c.get("editable")
    }
    try:
        outcome = run_data_blocks(
            db, cell_dicts, ref_answers, checker_code, default_dataset=dataset
        )
    except requests.RequestException as exc:
        return (f"Could not reach ml-runner to validate the problem: {exc}", 502)
    if outcome["status"] == "passed":
        return None
    # Point the examiner at the first block their own reference answers fail.
    bad = next((c for c in outcome["cell_results"] if c["status"] != "passed"), None)
    where = f" (block at cell #{bad['index']}, {bad['status']})" if bad else ""
    if outcome["status"] == "error":
        return (
            "Your reference notebook crashed before its checks ran — fix a "
            f"cell or its check before uploading{where}.\n{outcome['feedback']}",
            400,
        )
    return (
        "Your reference answers don't satisfy their own per-block checks — fix a "
        f"cell or its check before uploading{where}.\n{outcome['feedback']}",
        400,
    )


def data_problem_detail(
    db: Session, problem: DataProblem, include_secret: bool = False
) -> dict:
    return {
        "id": problem.id,
        "slug": problem.slug,
        "title": problem.title,
        "description": problem.description,
        "difficulty": problem.difficulty,
        "cells": (
            (problem.cells or [])
            if include_secret
            else student_cells(problem.cells or [])
        ),
        "dataset": problem.dataset.slug if problem.dataset else None,
        "dataset_columns": dataset_columns(problem.dataset) if problem.dataset else [],
        "checker_code": problem.checker_code if include_secret else "",
    }


def create_data_problem(
    db: Session, data, owner_id: uuid.UUID | None = None
) -> DataProblem:
    dataset = _dataset_for(db, data.dataset_slug)
    problem = DataProblem(
        slug=data.slug,
        title=data.title,
        description=data.description,
        difficulty=data.difficulty,
        cells=[c.model_dump() for c in data.cells],
        checker_code=data.checker_code,
        dataset_id=dataset.id if dataset else None,
        owner_id=owner_id,
    )
    db.add(problem)
    db.commit()
    db.refresh(problem)
    return problem


def update_data_problem(db: Session, problem: DataProblem, data) -> DataProblem:
    dataset = _dataset_for(db, data.dataset_slug)
    problem.title = data.title
    problem.description = data.description
    problem.difficulty = data.difficulty
    problem.cells = [c.model_dump() for c in data.cells]
    problem.checker_code = data.checker_code
    problem.dataset_id = dataset.id if dataset else None
    db.commit()
    db.refresh(problem)
    return problem


# ---------- notebooks (saved, runnable, ungraded) ----------
def _strip_solutions(cells: list) -> list:
    """Remove reference solutions before returning a notebook to a solver — they
    still see the code cell (their blank), its kind, and its points, never the
    answer. Also blanks the code `source` so authored starters aren't leaked as
    answers (authors keep an example there via… well, they just author them)."""
    out = []
    for c in cells:
        c = dict(c)
        c.pop("solution", None)
        out.append(c)
    return out


def notebook_detail(
    notebook: Notebook, is_owner: bool = False, include_solution: bool = False
) -> dict:
    cells = notebook.cells or []
    return {
        "id": notebook.id,
        "slug": notebook.slug,
        "title": notebook.title,
        "cells": cells if include_solution else _strip_solutions(cells),
        "is_owner": is_owner,
        "can_author": include_solution,
        "is_public": notebook.is_public,
    }


def create_notebook(db: Session, data, owner_id: uuid.UUID | None = None) -> Notebook:
    notebook = Notebook(
        slug=data.slug,
        title=data.title,
        cells=[c.model_dump() for c in data.cells],
        is_public=bool(getattr(data, "is_public", False)),
        owner_id=owner_id,
    )
    db.add(notebook)
    db.commit()
    db.refresh(notebook)
    return notebook


def update_notebook(db: Session, notebook: Notebook, data) -> Notebook:
    notebook.title = data.title
    notebook.cells = [c.model_dump() for c in data.cells]
    notebook.is_public = bool(getattr(data, "is_public", notebook.is_public))
    db.commit()
    db.refresh(notebook)
    return notebook


def run_notebook_cell(
    db: Session, cells: list, index: int, default_dataset: "Dataset | None" = None
) -> dict:
    """Run the notebook's code cells 0..index (markdown skipped) as one program in
    a shared namespace and return the cell-at-`index`'s output. ml-runner is
    stateless, so earlier cells re-run each time. Datasets referenced via
    `load_dataset("slug")` (plus `default_dataset`, bound to `df`) are embedded.
    Returns ml-runner's {stdout, error, images}."""
    parts = []
    for i, c in enumerate(cells):
        if i > index:
            break
        if c.get("kind") != "code":
            continue
        src = c.get("source", "")
        if i == index:
            src = echo_last_expression(src)  # show the run cell's final expression
        parts.append(src)
    program = "\n".join(parts)
    preamble = build_dataset_loader(db, program, default_dataset)
    return mlrunner.run(preamble + "\n" + program, "")


def _output_matches(student: str, expected: str) -> bool:
    """A block passes when its output equals the solution's output, or contains it
    (both trimmed). An empty expected output can't be graded — never a pass."""
    s, e = (student or "").strip(), (expected or "").strip()
    if not e:
        return False
    return s == e or e in s


def check_notebook_cell(
    db: Session, cells: list, index: int, default_dataset: "Dataset | None" = None
) -> dict:
    """Auto-check one code cell against its solution. Runs the notebook up to
    `index` twice — once with the student's code, once with the cell's `solution`
    swapped in — and compares the cell's output (equals OR contains). Returns
    {status, points, points_earned, stdout, expected, images, feedback}."""
    if index < 0 or index >= len(cells) or cells[index].get("kind") != "code":
        return {
            "status": "error",
            "feedback": "That cell can't be checked.",
            "stdout": "",
            "expected": "",
            "images": [],
            "points": 0,
            "points_earned": 0,
        }
    solution = (cells[index].get("solution") or "").strip()
    points = int(cells[index].get("points") or 0)
    if not solution:
        return {
            "status": "error",
            "points": points,
            "points_earned": 0,
            "stdout": "",
            "expected": "",
            "images": [],
            "feedback": "Add a solution to this cell so it can be auto-checked.",
        }

    student_res = run_notebook_cell(db, cells, index, default_dataset)
    if (student_res.get("error") or "").strip():
        return {
            "status": "error",
            "points": points,
            "points_earned": 0,
            "stdout": student_res.get("stdout", ""),
            "expected": "",
            "images": student_res.get("images", []),
            "feedback": student_res["error"],
        }
    student_out = student_res.get("stdout") or ""

    expected_cells = [dict(c) for c in cells]
    expected_cells[index] = {**expected_cells[index], "source": solution}
    expected_res = run_notebook_cell(db, expected_cells, index, default_dataset)
    expected_out = expected_res.get("stdout") or ""

    passed = _output_matches(student_out, expected_out)
    return {
        "status": "passed" if passed else "failed",
        "points": points,
        "points_earned": points if passed else 0,
        "stdout": student_out,
        "expected": expected_out,
        "images": student_res.get("images", []),
        "feedback": (
            f"Matches the solution output. +{points} pts"
            if passed
            else "Output doesn't match the solution yet."
        ),
    }


def is_notebook_cells(cells: list) -> bool:
    """True when a cell list is notebook-format (output-graded via `solution`)
    rather than the legacy data-problem format (assertion-graded via editable/check)."""
    return any(isinstance(c, dict) and "solution" in c for c in (cells or []))


def grade_notebook_cells(
    db: Session,
    cells: list,
    answers_by_index: dict[int, str],
    default_dataset: "Dataset | None" = None,
) -> tuple[int, int, list]:
    """Grade notebook-format cells: substitute the learner's code, auto-check every
    graded cell (one with a solution) by output comparison, and return
    (score, max_score, cell_results). Shared by standalone notebooks and exams."""
    effective = []
    for i, c in enumerate(cells):
        c = dict(c)
        if c.get("kind") == "code" and i in answers_by_index:
            c["source"] = answers_by_index[i]
        effective.append(c)

    graded = [
        i
        for i, c in enumerate(cells)
        if c.get("kind") == "code" and (c.get("solution") or "").strip()
    ]
    max_score = sum(int(cells[i].get("points") or 0) for i in graded)

    cell_results = []
    score = 0
    for i in graded:
        res = check_notebook_cell(db, effective, i, default_dataset)
        pts = int(cells[i].get("points") or 0)
        earned = pts if res.get("status") == "passed" else 0
        score += earned
        cell_results.append(
            {
                "index": i,
                "status": res.get("status"),
                "feedback": res.get("feedback", ""),
                "points": pts,
                "points_earned": earned,
                # Stored so an examiner can review the exact code and its output/error.
                "source": effective[i].get("source", ""),
                "stdout": res.get("stdout", ""),
            }
        )
    return score, max_score, cell_results


def submit_notebook(
    db: Session, notebook: Notebook, answers, user=None
) -> tuple[NotebookSubmission, int]:
    """Grade a learner's submission: substitute their code into each code cell,
    then auto-check every graded cell (one with a solution) via output comparison.
    Stores and returns (submission, http_status)."""
    cells = notebook.cells or []
    answers_by_index = {a.index: a.source for a in answers}
    graded = [
        i
        for i, c in enumerate(cells)
        if c.get("kind") == "code" and (c.get("solution") or "").strip()
    ]

    submission = NotebookSubmission(
        notebook_id=notebook.id,
        user_id=user.id if user else None,
        answers={str(k): v for k, v in answers_by_index.items()},
        max_score=sum(int(cells[i].get("points") or 0) for i in graded),
    )
    db.add(submission)
    db.flush()

    if not settings.MLRUNNER_URL:
        submission.status = "error"
        db.commit()
        db.refresh(submission)
        return submission, 503

    try:
        score, max_score, cell_results = grade_notebook_cells(
            db, cells, answers_by_index
        )
    except requests.RequestException:
        submission.status = "error"
        db.commit()
        db.refresh(submission)
        return submission, 502

    submission.score = score
    submission.max_score = max_score
    submission.cell_results = cell_results
    submission.status = "passed" if (max_score > 0 and score == max_score) else "failed"
    db.commit()
    db.refresh(submission)
    return submission, 200


def notebook_submission_detail(
    sub: NotebookSubmission, include_email: bool = False, include_grades: bool = False
) -> dict:
    """A solver only learns that their attempt was recorded — never the score, which
    cells passed, or the answers (that would leak the solution). Only authors
    (`include_grades`) get the full breakdown."""
    base = {
        "id": str(sub.id),
        "notebook_id": str(sub.notebook_id),
        "created_at": sub.created_at.isoformat() if sub.created_at else "",
    }
    if not include_grades:
        base["status"] = "submitted"
        return base
    base.update(
        {
            "status": sub.status,
            "score": sub.score,
            "max_score": sub.max_score,
            "cell_results": sub.cell_results or [],
            "answers": sub.answers or {},
            "user_email": (sub.user.email if include_email and sub.user else None),
        }
    )
    return base


def check_data_block(
    db: Session, problem: DataProblem, answers, index: int
) -> tuple[dict, int]:
    """Run the notebook cumulatively up to and including the editable cell at
    `index`, then run that cell's check — so a student can test one block without
    submitting the whole notebook. Returns ({status, feedback, stdout, images}, http).
    """
    cells = problem.cells or []
    if index < 0 or index >= len(cells) or not cells[index].get("editable"):
        return {
            "status": "error",
            "feedback": "That block can't be checked.",
            "stdout": "",
            "images": [],
        }, 400
    if not settings.MLRUNNER_URL:
        return {
            "status": "error",
            "feedback": "ml-runner is not configured.",
            "stdout": "",
            "images": [],
        }, 503

    answers_by_index = {a.index: a.source for a in answers}
    prefix = cells[: index + 1]  # indices stay aligned with the full notebook
    try:
        outcome = run_data_blocks(
            db, prefix, answers_by_index, default_dataset=problem.dataset
        )
    except requests.RequestException as exc:
        return {
            "status": "error",
            "feedback": f"ml-runner request failed: {exc}",
            "stdout": "",
            "images": [],
        }, 502

    this_block = next((c for c in outcome["cell_results"] if c["index"] == index), None)
    status = this_block["status"] if this_block else outcome["status"]
    feedback = this_block["feedback"] if this_block else outcome["feedback"]
    return {
        "status": status,
        "feedback": feedback,
        "stdout": outcome["stdout"],
        "images": outcome["images"],
    }, 200


def submit_data_problem(
    db: Session, problem: DataProblem, answers, user=None
) -> tuple[DataSubmission, int]:
    """Assemble the student's notebook (examiner cells + their fills) and grade it
    through ml-runner. `answers` is a list of DataAnswerIn. Returns (submission, status).
    """
    answers_by_index = {a.index: a.source for a in answers}
    source_code = _assemble_notebook(problem.cells or [], answers_by_index)

    cells = problem.cells or []
    text_answers = {
        str(a.index): a.source
        for a in answers
        if 0 <= a.index < len(cells)
        and cells[a.index].get("kind") == "text"
        and a.source.strip()
    }

    submission = DataSubmission(
        data_problem_id=problem.id,
        source_code=source_code,
        text_answers=text_answers,
        user_id=user.id if user else None,
    )
    db.add(submission)
    db.flush()

    if not settings.MLRUNNER_URL:
        submission.status = "error"
        submission.feedback = (
            "ml-runner is not configured — ask an admin to set MLRUNNER_URL."
        )
        db.commit()
        db.refresh(submission)
        return submission, 503

    try:
        outcome = run_data_blocks(
            db,
            problem.cells or [],
            answers_by_index,
            problem.checker_code,
            default_dataset=problem.dataset,
        )
    except requests.RequestException as exc:
        submission.status = "error"
        submission.feedback = f"ml-runner request failed: {exc}"
        db.commit()
        db.refresh(submission)
        return submission, 502

    points_by_index = _editable_points(problem.cells or [])
    cell_results = []
    score = 0
    for cr in outcome["cell_results"]:
        pts = points_by_index.get(cr["index"], 0)
        earned = pts if cr["status"] == "passed" else 0
        score += earned
        cell_results.append({**cr, "points": earned, "max_points": pts})
    max_score = sum(points_by_index.values())

    submission.status = outcome["status"]
    submission.stdout = outcome["stdout"]
    submission.feedback = outcome["feedback"]
    submission.images = outcome["images"]
    submission.cell_results = cell_results
    submission.score = score
    submission.max_score = max_score
    db.commit()
    db.refresh(submission)
    return submission, 200


# ---------- exams ----------
def _resolve_fk(db: Session, q: "QuestionWriteIn"):
    dataset = None
    if q.dataset_slug:
        dataset = db.query(Dataset).filter(Dataset.slug == q.dataset_slug).first()
    problem = None
    if q.problem_slug:
        problem = db.query(Problem).filter(Problem.slug == q.problem_slug).first()
    data_problem = None
    if q.data_problem_slug:
        # Data problems are now the notebook-based ones.
        data_problem = (
            db.query(Notebook).filter(Notebook.slug == q.data_problem_slug).first()
        )
    return dataset, problem, data_problem


def write_questions(db: Session, exam: Exam, questions: list) -> None:
    for q in list(exam.questions):
        db.delete(q)
    db.flush()
    for idx, q in enumerate(questions):
        if q.type == "mcq":
            if len(q.choices) < 2:
                raise AcademyError("An MCQ needs at least two choices.")
            if not any(c.get("correct") for c in q.choices):
                raise AcademyError("Mark at least one choice as correct.")
        dataset, problem, data_problem = _resolve_fk(db, q)
        question = Question(
            exam_id=exam.id,
            order=idx,
            type=q.type,
            title=q.title,
            body=q.body,
            points=q.points,
            choices=q.choices,
            multiple=q.multiple,
            starter_code=q.starter_code,
            dataset_id=dataset.id if dataset else None,
        )
        db.add(question)
        db.flush()  # need question.id before creating related records

        if q.type == "code" and problem is not None:
            exam_problem = ExamProblem(
                question_id=question.id,
                title=problem.title,
                description=problem.description,
                difficulty=problem.difficulty,
                language=problem.language,
                starter_code=problem.starter_code,
                reference_solution=problem.reference_solution,
                problem_slug=problem.slug,
            )
            db.add(exam_problem)
            db.flush()
            for tc in problem.test_cases:
                db.add(
                    ExamProblemTestCase(
                        exam_problem_id=exam_problem.id,
                        stdin=tc.stdin,
                        expected_output=tc.expected_output,
                        is_sample=tc.is_sample,
                    )
                )

        if q.type == "datalab" and data_problem is not None:
            # Copy the notebook's cells (with solutions + points) so the exam is
            # self-contained and graded by output comparison. Also inherit its
            # title (when the examiner left one blank) and set the question's worth
            # to the sum of its graded cells' points.
            cells = data_problem.cells or []
            question.data_problem_cells = cells
            question.data_problem_checker_code = ""
            question.data_problem_slug = data_problem.slug
            # A data-problem question always shows the data problem's own title.
            question.title = data_problem.title or q.title
            question.points = (
                sum(
                    int(c.get("points", 0) or 0)
                    for c in cells
                    if c.get("kind") == "code" and (c.get("solution") or "").strip()
                )
                or question.points
            )


def create_exam(db: Session, data, owner_id: uuid.UUID | None = None) -> Exam:
    exam = Exam(
        title=data.title,
        slug=data.slug,
        description=data.description,
        is_published=data.is_published,
        access_type=data.access_type,
        owner_id=owner_id,
    )
    db.add(exam)
    db.flush()
    write_questions(db, exam, data.questions)
    db.commit()
    db.refresh(exam)
    return exam


def update_exam(db: Session, exam: Exam, data) -> Exam:
    exam.title = data.title
    exam.description = data.description
    exam.is_published = data.is_published
    exam.access_type = data.access_type
    write_questions(db, exam, data.questions)
    db.commit()
    db.refresh(exam)
    return exam


def question_read_payload(q: Question, include_correct: bool = False) -> dict:
    """Strip the `correct` flag from choices (unless include_correct — used for exam authors)."""
    exam_problem = None
    if q.exam_problem:
        ep = q.exam_problem
        exam_problem = {
            "id": ep.id,
            "title": ep.title,
            "description": ep.description,
            "difficulty": ep.difficulty,
            "language": ep.language,
            "starter_code": ep.starter_code,
            "sample_test_cases": [tc for tc in ep.test_cases if tc.is_sample],
            "problem_slug": ep.problem_slug,
        }
    elif q.problem:
        ep = q.problem
        exam_problem = {
            "id": ep.id,
            "title": ep.title,
            "description": ep.description,
            "difficulty": ep.difficulty,
            "language": ep.language,
            "starter_code": ep.starter_code,
            "sample_test_cases": [tc for tc in ep.test_cases if tc.is_sample],
            "problem_slug": ep.slug,
        }
    data_problem_cells = (
        student_cells(q.data_problem_cells) if q.data_problem_cells else []
    )
    data_problem_dataset = (
        q.dataset.slug if q.dataset and q.data_problem_cells else None
    )
    choices = q.choices or []
    if include_correct:
        choice_out = [
            {"text": c.get("text", ""), "correct": c.get("correct", False)}
            for c in choices
        ]
    else:
        choice_out = [{"text": c.get("text", "")} for c in choices]
    return {
        "id": q.id,
        "order": q.order,
        "type": q.type,
        "title": q.title,
        "body": q.body,
        "points": q.points,
        "choices": choice_out,
        "multiple": q.multiple,
        "starter_code": q.starter_code,
        "dataset": q.dataset.slug if q.dataset else None,
        "problem": None,
        "exam_problem": exam_problem,
        "data_problem_cells": data_problem_cells,
        "data_problem_dataset": data_problem_dataset,
        "problem_slug": (
            exam_problem["problem_slug"]
            if exam_problem and "problem_slug" in exam_problem
            else None
        ),
        "data_problem_slug": q.data_problem_slug,
    }


def check_answer(question: Question, selected: list[int]) -> dict:
    selected_set = set(selected)
    correct = {i for i, c in enumerate(question.choices or []) if c.get("correct")}
    is_correct = selected_set == correct and bool(correct)
    return {
        "correct": is_correct,
        "correct_indices": sorted(correct),
        "points": question.points if is_correct else 0,
    }


# ---------- exam attempts (persisted, one per user+exam) ----------
def _split_points(total: int, n: int) -> list[int]:
    """Split `total` points as evenly as possible into `n` whole-point shares.
    The rounding remainder goes to the last share so the shares sum to `total`."""
    if n <= 0:
        return []
    base = total // n
    shares = [base] * n
    shares[-1] += total - base * n
    return shares


def _grade_exam_question(db: Session, q: Question, ans) -> dict:
    """Grade one question against a taker's answer (an ExamAnswerIn or None).
    Returns the per-question breakdown dict stored in ExamAttempt.answers.

    MCQ and code questions auto-grade; text/datalab are stored ungraded (no
    auto-checker exists for them) so a human can review them from the breakdown.
    """
    entry = {
        "question_id": str(q.id),
        "order": q.order,
        "type": q.type,
        "title": q.title,
        "points": q.points,
        "answer": None,
        "points_earned": 0,
        "graded": False,
        "detail": None,
    }

    if q.type == "mcq":
        selected = list(ans.selected) if ans else []
        res = check_answer(q, selected)
        entry.update(
            answer=selected,
            graded=True,
            points_earned=res["points"],
            detail={
                "correct": res["correct"],
                "correct_indices": res["correct_indices"],
            },
        )
        return entry

    if q.type == "code":
        source = (ans.source_code if ans else "") or ""
        test_cases, language = [], (ans.language if ans else None)
        if q.exam_problem:
            test_cases = list(q.exam_problem.test_cases)
            language = language or q.exam_problem.language
        elif q.problem:
            test_cases = list(q.problem.test_cases)
            language = language or q.problem.language
        entry["answer"] = source
        if not source.strip() or not test_cases:
            entry.update(graded=True, detail={"note": "empty answer or no test cases"})
            return entry
        try:
            passed, total, detail = judge0.run_against_testcases(
                language, source, test_cases
            )
        except requests.RequestException as exc:
            entry.update(
                graded=False, detail={"error": f"Judge0 request failed: {exc}"}
            )
            return entry
        entry.update(
            graded=True,
            detail={"passed": passed, "total": total},
            points_earned=q.points if total and passed == total else 0,
        )
        return entry

    # text — stored, ungraded.
    if q.type == "text":
        entry["answer"] = (ans.text if ans else "") or ""
        return entry

    # datalab — grade each graded cell; sum the cells' points as this question's worth.
    source = (ans.source_code if ans else "") or ""
    entry["answer"] = source
    cells = q.data_problem_cells or []
    checker_code = q.data_problem_checker_code or ""
    answers_by_index = {c.index: c.source for c in (ans.cells if ans else [])}
    if not cells or (not source.strip() and not answers_by_index):
        entry.update(
            graded=True, detail={"note": "empty answer or no data problem configured"}
        )
        return entry

    # Notebook-based data problems grade by output comparison against each cell's
    # solution; the question's worth is the sum of its cells' points.
    if is_notebook_cells(cells):
        try:
            score, max_score, cell_results = grade_notebook_cells(
                db, cells, answers_by_index
            )
        except requests.RequestException as exc:
            entry.update(
                graded=False, detail={"error": f"ml-runner request failed: {exc}"}
            )
            return entry
        entry["points"] = max_score
        entry.update(
            graded=True,
            points_earned=score,
            detail={
                "cells": [
                    {
                        "index": r["index"],
                        "status": r["status"],
                        "feedback": r["feedback"],
                        "points": r["points_earned"],
                        "max_points": r["points"],
                        "answer": r["source"],
                        "stdout": r["stdout"],
                    }
                    for r in cell_results
                ]
            },
        )
        return entry

    try:
        outcome = run_data_blocks(
            db, cells, answers_by_index, checker_code, default_dataset=q.dataset
        )
    except requests.RequestException as exc:
        entry.update(graded=False, detail={"error": f"ml-runner request failed: {exc}"})
        return entry

    cell_results = outcome["cell_results"]
    # Prefer the per-block points authored on the data problem's cells; fall back
    # to an even split of q.points for older problems that carry no per-block points.
    authored = _editable_points(cells)
    if any(authored.values()):
        points = [authored.get(cr["index"], 0) for cr in cell_results]
        entry["points"] = sum(authored.values())
    else:
        points = _split_points(q.points, len(cell_results))
    per_cell = []
    earned = 0
    for k, cr in enumerate(cell_results):
        pts = points[k]
        passed = cr["status"] == "passed"
        if passed:
            earned += pts
        per_cell.append(
            {
                "index": cr["index"],
                "status": cr["status"],
                "feedback": cr["feedback"],
                "points": pts if passed else 0,
                "max_points": pts,
                "answer": answers_by_index.get(cr["index"], ""),
            }
        )
    entry.update(
        graded=True,
        points_earned=earned,
        detail={
            "status": outcome["status"],
            "stdout": outcome["stdout"],
            "feedback": outcome["feedback"],
            "cells": per_cell,
        },
    )
    return entry


def submit_exam_attempt(db: Session, user, exam: Exam, answers) -> ExamAttempt:
    """Grade a whole-exam submission and append a new finalized attempt.

    Multiple submissions are allowed — each call creates a fresh
    ``ExamAttempt`` row with ``submitted=True`` and a ``submitted_at``
    timestamp.  Previous attempts (draft or submitted) are left untouched.
    """
    ans_by_qid = {str(a.question_id): a for a in answers}
    breakdown = [
        _grade_exam_question(db, q, ans_by_qid.get(str(q.id)))
        for q in sorted(exam.questions, key=lambda x: x.order)
    ]
    score = sum(e["points_earned"] for e in breakdown)
    max_score = sum(e["points"] for e in breakdown if e["graded"])

    attempt = ExamAttempt(
        user_id=user.id if user else None,
        exam_id=exam.id,
        score=score,
        max_score=max_score,
        answers=breakdown,
        submitted=True,
        submitted_at=datetime.now(timezone.utc),
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return attempt


def exam_question_reference(q: "Question") -> dict | None:
    """The reference solution for a question, for an examiner's review only — this
    is never included in what a student receives. Shape depends on the type:
    code → {"solution": str}; datalab → {"cells": {index: solutionCode}};
    mcq → {"correct": [choice text, …]}."""
    if q.type == "code":
        ref = ""
        if q.exam_problem:
            ref = q.exam_problem.reference_solution or ""
        elif q.problem:
            ref = q.problem.reference_solution or ""
        return {"type": "code", "solution": ref} if ref.strip() else None
    if q.type == "datalab":
        cells = q.data_problem_cells or []
        sols = {
            str(i): c.get("solution", "")
            for i, c in enumerate(cells)
            if c.get("kind") == "code" and (c.get("solution") or "").strip()
        }
        return {"type": "datalab", "cells": sols} if sols else None
    if q.type == "mcq":
        correct = [c.get("text", "") for c in (q.choices or []) if c.get("correct")]
        return {"type": "mcq", "correct": correct}
    return None


def exam_percentage(score: int, max_score: int) -> float:
    """Overall grade as a percentage of 100 — each question's points are its weight,
    so the total earned is normalized across every graded problem in the exam."""
    return round(score / max_score * 100, 1) if max_score else 0.0


def exam_attempt_payload(
    attempt: ExamAttempt, include_breakdown: bool = True, hide_scores: bool = False
) -> dict:
    if hide_scores:
        answers = []
        if include_breakdown and attempt.answers:
            answers = [
                {
                    "question_id": a.get("question_id"),
                    "order": a.get("order"),
                    "type": a.get("type"),
                    "title": a.get("title"),
                }
                for a in attempt.answers
            ]
        return {
            "id": str(attempt.id),
            "exam_slug": attempt.exam.slug if attempt.exam else "",
            "exam_title": attempt.exam.title if attempt.exam else "",
            "score": 0,
            "max_score": 0,
            "percentage": 0.0,
            "submitted": bool(attempt.submitted),
            "answers": answers,
            "submitted_at": (attempt.submitted_at or attempt.updated_at).isoformat(),
        }

    return {
        "id": str(attempt.id),
        "exam_slug": attempt.exam.slug if attempt.exam else "",
        "exam_title": attempt.exam.title if attempt.exam else "",
        "score": attempt.score,
        "max_score": attempt.max_score,
        "percentage": exam_percentage(attempt.score, attempt.max_score),
        "submitted": bool(attempt.submitted),
        "answers": attempt.answers if include_breakdown else [],
        "submitted_at": (attempt.submitted_at or attempt.updated_at).isoformat(),
    }


def upsert_question_grade(
    db: Session, user, exam: Exam, question_id: uuid.UUID, answer_data
) -> ExamAttempt:
    """Grade a single question and update the user's latest draft attempt.

    Used for per-question grading (MCQ check, code run) so the attempt stays
    live.  If the latest attempt is already submitted a fresh draft is created.
    """
    q = db.get(Question, question_id)
    if not q or q.exam_id != exam.id:
        raise AcademyError("Question not found in this exam.")

    # Build a stub answer object so _grade_exam_question works.
    class _StubAnswer:
        pass

    stub = _StubAnswer()
    if q.type == "mcq":
        stub.selected = (
            answer_data.selected
            if hasattr(answer_data, "selected")
            else answer_data.get("selected", [])
        )
    elif q.type == "code":
        stub.source_code = (
            answer_data.source_code
            if hasattr(answer_data, "source_code")
            else answer_data.get("source_code", "")
        )
        stub.language = (
            answer_data.language
            if hasattr(answer_data, "language")
            else answer_data.get("language", None)
        )
    elif q.type == "text":
        stub.text = (
            answer_data.text
            if hasattr(answer_data, "text")
            else answer_data.get("text", "")
        )
    elif q.type == "datalab":
        stub.source_code = (
            answer_data.source_code
            if hasattr(answer_data, "source_code")
            else answer_data.get("source_code", "")
        )
        stub.cells = (
            answer_data.cells
            if hasattr(answer_data, "cells")
            else answer_data.get("cells", [])
        )

    grade = _grade_exam_question(db, q, stub)

    # Find the latest draft attempt (submitted=False) to update in-place.
    draft = (
        db.query(ExamAttempt)
        .filter(
            ExamAttempt.user_id == (user.id if user else None),
            ExamAttempt.exam_id == exam.id,
            ExamAttempt.submitted.is_(False),
        )
        .order_by(ExamAttempt.updated_at.desc())
        .first()
    )

    if draft:
        existing = {e["question_id"]: e for e in (draft.answers or [])}
        existing[str(question_id)] = grade
        merged = [existing[k] for k in sorted(existing)]
        draft.answers = merged
        draft.score = sum(e["points_earned"] for e in merged)
        draft.max_score = sum(e["points"] for e in merged if e["graded"])
        db.commit()
        db.refresh(draft)
        return draft

    # No prior draft — create one with just this question.
    breakdown = [grade]
    attempt = ExamAttempt(
        user_id=user.id if user else None,
        exam_id=exam.id,
        score=sum(e["points_earned"] for e in breakdown),
        max_score=sum(e["points"] for e in breakdown if e["graded"]),
        answers=breakdown,
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return attempt
