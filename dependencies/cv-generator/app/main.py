import hashlib
import json

from fastapi import FastAPI

from app.schemas import CVGenerateRequest, CVGenerateResponse
from app.snapshot import allowlist_snapshot, build_profile_cv_snapshot
from app.templates import render

app = FastAPI(title="CV Generator")


def _snapshot_sha256(snapshot: dict) -> str:
    payload = json.dumps(snapshot, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _build_snapshot(request: CVGenerateRequest) -> dict:
    """Build an allow-listed snapshot from the backend payload."""
    user = request.user.model_dump()
    prefs = request.prefs.model_dump() if request.prefs else None
    target_role = request.target_role or "backend-developer"

    if request.source == "hstaff":
        talent = request.talent.model_dump()
        return allowlist_snapshot(
            user=user,
            talent=talent,
            target_role=target_role,
            prefs=prefs,
            public=request.public,
        )

    # source == "profile"
    profile = request.profile.model_dump()
    talent = request.talent.model_dump()
    return build_profile_cv_snapshot(
        user=user,
        profile=profile,
        talent=talent,
        target_role=target_role,
        prefs=prefs,
        public=request.public,
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/generate", response_model=CVGenerateResponse)
def generate(request: CVGenerateRequest):
    """Generate a CV from the supplied snapshot or raw payload."""
    snapshot = request.snapshot or _build_snapshot(request)
    sha256 = _snapshot_sha256(snapshot)
    html = render(snapshot, snapshot.get("target_role") or "backend-developer")
    return CVGenerateResponse(html=html, snapshot=snapshot, sha256=sha256)
