"""Thin client for the Judge0 API."""

import base64
import time

import requests

from app.academy.models import JUDGE0_LANGUAGE_IDS
from app.core.config import settings


def _headers():
    headers = {"Content-Type": "application/json"}
    if settings.JUDGE0_AUTH_TOKEN:
        headers[settings.JUDGE0_AUTH_HEADER] = settings.JUDGE0_AUTH_TOKEN
    return headers


def _b64(value: str) -> str:
    return base64.b64encode((value or "").encode()).decode()


def _b64decode(value):
    if not value:
        return ""
    return base64.b64decode(value).decode(errors="replace")


def run_once(language: str, source_code: str, stdin: str = ""):
    """Run source_code once (playground / Colab-style). Returns a result dict.

    No test-case comparison — just execute and return stdout/stderr/status.
    """
    language_id = JUDGE0_LANGUAGE_IDS[language]

    base = settings.JUDGE0_URL.rstrip("/")
    payload = {
        "language_id": language_id,
        "source_code": _b64(source_code),
        "stdin": _b64(stdin),
        # Run without cgroups (required on cgroup-v2 hosts).
        "enable_per_process_and_thread_time_limit": True,
        "enable_per_process_and_thread_memory_limit": True,
    }
    resp = requests.post(
        f"{base}/submissions?base64_encoded=true&wait=true",
        json=payload,
        headers=_headers(),
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    status = data.get("status") or {}
    return {
        "status": status.get("description"),
        "status_id": status.get("id"),
        "stdout": _b64decode(data.get("stdout")),
        "stderr": _b64decode(data.get("stderr")),
        "compile_output": _b64decode(data.get("compile_output")),
        "time": data.get("time"),
        "memory": data.get("memory"),
    }


def run_against_testcases(language: str, source_code: str, test_cases):
    """Run source_code for each test case via Judge0 batch submission.

    `test_cases` is a list of objects with .id, .stdin, .expected_output, .is_sample.
    Returns (passed, total, detail_list).
    """
    language_id = JUDGE0_LANGUAGE_IDS[language]

    submissions = []
    for tc in test_cases:
        if language == "sql":
            # SQLite pipes the source file into sqlite3 and ignores stdin,
            # so the per-testcase schema/data must be prepended to the query.
            effective_source = f"{tc.stdin}\n{source_code}" if tc.stdin else source_code
            effective_stdin = ""
        else:
            effective_source = source_code
            effective_stdin = tc.stdin
        submissions.append(
            {
                "language_id": language_id,
                "source_code": _b64(effective_source),
                "stdin": _b64(effective_stdin),
                "expected_output": _b64(tc.expected_output.rstrip("\n")),
                # Both flags true => isolate runs without cgroups, which is
                # required on cgroup-v2 hosts (see judge0 isolate_job.rb).
                "enable_per_process_and_thread_time_limit": True,
                "enable_per_process_and_thread_memory_limit": True,
            }
        )

    base = settings.JUDGE0_URL.rstrip("/")
    resp = requests.post(
        f"{base}/submissions/batch?base64_encoded=true&wait=true",
        json={"submissions": submissions},
        headers=_headers(),
        timeout=60,
    )
    resp.json()  # raise on non-json
    resp.raise_for_status()

    # wait=true is not guaranteed for batch; poll by tokens if needed.
    tokens = [item["token"] for item in resp.json()]
    results = _fetch_batch(base, tokens)

    detail = []
    passed = 0
    for tc, result in zip(test_cases, results):
        status_id = (result.get("status") or {}).get("id")
        status_desc = (result.get("status") or {}).get("description")
        stdout = _b64decode(result.get("stdout")).rstrip("\n")
        expected = tc.expected_output.rstrip("\n")
        stderr = _b64decode(result.get("stderr"))
        compile_output = _b64decode(result.get("compile_output"))
        ok = status_id == 3 and stdout == expected
        if ok:
            passed += 1
        detail.append(
            {
                "test_case_id": str(tc.id),
                "is_sample": tc.is_sample,
                "status": status_desc,
                "status_id": status_id,
                "passed": ok,
                "stdout": stdout if tc.is_sample else None,
                "expected": expected if tc.is_sample else None,
                "stderr": stderr or compile_output,
            }
        )

    return passed, len(test_cases), detail


def _fetch_batch(base, tokens, attempts=30, delay=0.5):
    joined = ",".join(tokens)
    url = (
        f"{base}/submissions/batch?tokens={joined}&base64_encoded=true"
        "&fields=stdout,stderr,compile_output,status"
    )
    for _ in range(attempts):
        resp = requests.get(url, headers=_headers(), timeout=60)
        resp.raise_for_status()
        submissions = resp.json()["submissions"]
        # status id 1 = In Queue, 2 = Processing
        if all((s.get("status") or {}).get("id", 0) not in (1, 2) for s in submissions):
            return submissions
        time.sleep(delay)
    return submissions
