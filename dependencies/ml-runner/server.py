"""ML runner HTTP service.

Executes untrusted Python (numpy/pandas/scikit-learn/matplotlib) against an
admin-uploaded CSV. Each request runs in a fresh subprocess with:
  - CPU + address-space (memory) + file-size rlimits
  - its own session/process group (killed as a group on timeout)
  - a non-root user (set in the Dockerfile)
  - no internet (the container is on an `internal` Docker network)
"""
import json
import os
import resource
import signal
import subprocess
import tempfile

from flask import Flask, jsonify, request

app = Flask(__name__)

DATASETS_DIR = "/datasets"
TIMEOUT = int(os.environ.get("ML_TIMEOUT", "120"))          # seconds
MEM_MB = int(os.environ.get("ML_MEM_MB", "1024"))            # address space cap
FSIZE_MB = int(os.environ.get("ML_FSIZE_MB", "50"))          # max file write


def _apply_limits():
    resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT, TIMEOUT + 5))
    fsize = FSIZE_MB * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.setsid()


def _resolve_dataset(name):
    if not name:
        return ""
    cand = os.path.realpath(os.path.join(DATASETS_DIR, name))
    if not (cand == DATASETS_DIR or cand.startswith(DATASETS_DIR + os.sep)):
        return None
    if not os.path.isfile(cand):
        return None
    return cand


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/run")
def run():
    data = request.get_json(force=True, silent=True) or {}
    code = data.get("code", "")
    dataset = data.get("dataset", "")

    data_path = _resolve_dataset(dataset)
    if data_path is None:
        return jsonify({
            "stdout": "",
            "error": "Dataset not found.",
            "images": [],
        })

    with tempfile.TemporaryDirectory() as work:
        user_path = os.path.join(work, "user_code.py")
        results_path = os.path.join(work, "_results.json")
        with open(user_path, "w") as fh:
            fh.write(code)

        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": work,
            "MPLBACKEND": "Agg",
            "MPLCONFIGDIR": work,
            "DATA_PATH": data_path,
            "USER_CODE": user_path,
            "RESULTS_PATH": results_path,
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
        }
        proc = subprocess.Popen(
            ["python", "/app/runner.py"],
            cwd=work, env=env, preexec_fn=_apply_limits,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        timed_out = False
        try:
            stdout, stderr = proc.communicate(timeout=TIMEOUT + 3)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = proc.communicate()

        error = ""
        images = []
        try:
            with open(results_path) as fh:
                result = json.load(fh)
                error = result.get("error", "")
                images = result.get("images", [])
        except Exception:
            pass

        if timed_out:
            error = (error + "\n" if error else "") + f"[Killed: exceeded {TIMEOUT}s time limit]"

        return jsonify({
            "stdout": stdout or "",
            "error": error,
            "images": images,
        })
