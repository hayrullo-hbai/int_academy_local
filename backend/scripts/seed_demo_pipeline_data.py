"""Populate the onboarding pipelines with a realistic mix of progress + outcomes
so the analytics views have something to chart.

Idempotent: it resets every pipeline's stages first, then re-applies a
deterministic distribution (keyed by row order), so re-running gives the same
result. Candidate `created_at` is left untouched — it already carries each
applicant's real application time from the Google-Sheet timestamp, so the
"who applied when" timeline stays intact; stage decisions are timestamped
*after* each candidate's own created_at.

Run:  docker compose exec backend python -m scripts.seed_demo_pipeline_data
"""

from datetime import timedelta, timezone

from sqlalchemy import select

from app.core.database import SessionLocal
from app.identity.models import User
from app.onboarding.enums import OnboardingStatus, StageOutcome
from app.onboarding.models import OnboardingPipeline, StageReport

PASSED = StageOutcome.PASSED.value
FAILED = StageOutcome.FAILED.value
PENDING = StageOutcome.PENDING.value

# Flow order. The three interviews are parallel, then discussion→payment→access.
FLOW = [
    "intro_call",
    "tech_interview",
    "culture_fit",
    "discussion",
    "payment",
    "access",
]


# A repeating 20-slot pattern → the overall mix across the board. Each entry maps
# stage key → outcome for that candidate; anything omitted stays pending.
#   onboarded  = all six passed
#   rejected_X = the stages up to X passed, X failed
#   prog_*     = still in progress, partway through
def _all_passed():
    return {k: PASSED for k in FLOW}


def _rejected_at(key):
    idx = FLOW.index(key)
    out = {k: PASSED for k in FLOW[:idx]}
    # For an interview rejection, only that interview is decided (others pending).
    if key in ("intro_call", "tech_interview", "culture_fit"):
        out = {}
    out[key] = FAILED
    return out


PATTERN = [
    ("onboarded", _all_passed()),
    ("onboarded", _all_passed()),
    ("onboarded", _all_passed()),
    ("rejected", _rejected_at("intro_call")),
    ("rejected", _rejected_at("tech_interview")),
    ("rejected", _rejected_at("culture_fit")),
    ("rejected", _rejected_at("discussion")),
    ("rejected", _rejected_at("payment")),
    ("in_progress", {}),  # fresh lead
    ("in_progress", {}),  # fresh lead
    ("in_progress", {}),  # fresh lead
    ("in_progress", {"intro_call": PASSED}),  # 1 interview done
    ("in_progress", {"intro_call": PASSED}),
    ("in_progress", {"intro_call": PASSED, "tech_interview": PASSED}),  # 2 interviews
    ("in_progress", {"intro_call": PASSED, "tech_interview": PASSED}),
    (
        "in_progress",
        {"intro_call": PASSED, "tech_interview": PASSED, "culture_fit": PASSED},
    ),  # 3 → discussion pending
    (
        "in_progress",
        {"intro_call": PASSED, "tech_interview": PASSED, "culture_fit": PASSED},
    ),
    (
        "in_progress",
        {
            "intro_call": PASSED,
            "tech_interview": PASSED,
            "culture_fit": PASSED,
            "discussion": PASSED,
        },
    ),  # payment pending
    (
        "in_progress",
        {
            "intro_call": PASSED,
            "tech_interview": PASSED,
            "culture_fit": PASSED,
            "discussion": PASSED,
        },
    ),
    (
        "in_progress",
        {
            "intro_call": PASSED,
            "tech_interview": PASSED,
            "culture_fit": PASSED,
            "discussion": PASSED,
            "payment": PASSED,
        },
    ),  # access pending
]

# Decision cadence: nth decided stage happens this many days after created_at.
_STEP_DAYS = [2, 5, 5, 9, 13, 17]


def _aware(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def run():
    db = SessionLocal()
    decider = db.execute(
        select(User).where(User.email == "manager@academy.local")
    ).scalar_one_or_none()
    decider_id = decider.id if decider else None

    pipelines = list(
        db.execute(
            select(OnboardingPipeline).order_by(OnboardingPipeline.created_at.asc())
        ).scalars()
    )

    counts = {"onboarded": 0, "rejected": 0, "in_progress": 0}
    for i, p in enumerate(pipelines):
        status, outcomes = PATTERN[i % len(PATTERN)]
        base = _aware(p.created_at)
        stages = {s.key: s for s in p.stages}

        # Reset this pipeline's stages + reports first (idempotent re-run).
        for s in stages.values():
            for r in list(s.reports):
                db.delete(r)
            s.outcome = PENDING
            s.decided_by_id = None
            s.decided_at = None
            s.zoom_link = ""

        # Apply the decided stages in flow order, timestamped after created_at.
        decided_keys = [k for k in FLOW if outcomes.get(k) in (PASSED, FAILED)]
        last_decided_at = None
        for step, key in enumerate(decided_keys):
            s = stages[key]
            s.outcome = outcomes[key]
            s.decided_at = base + timedelta(
                days=_STEP_DAYS[min(step, len(_STEP_DAYS) - 1)]
            )
            s.decided_by_id = decider_id
            last_decided_at = s.decided_at
            db.add(
                StageReport(
                    stage_id=s.id,
                    author_id=decider_id,
                    notes=f"{'Passed' if outcomes[key] == PASSED else 'Did not pass'} the {key.replace('_', ' ')} stage.",
                    outcome=outcomes[key],
                )
            )

        # Status + completion timestamp.
        if status == "onboarded":
            p.status = OnboardingStatus.ONBOARDED.value
            p.completed_at = last_decided_at
            p.scholarship_pct = [0, 25, 50, 75, 100][i % 5]
            p.payment_usd = [1200, 900, 600, 300, 0][i % 5]
            p.admission_fee_paid = True
        elif status == "rejected":
            p.status = OnboardingStatus.REJECTED.value
            p.completed_at = last_decided_at
        else:
            p.status = OnboardingStatus.IN_PROGRESS.value
            p.completed_at = None
            # Give the payment-stage folks a scholarship figure to chart.
            if outcomes.get("discussion") == PASSED:
                p.scholarship_pct = [50, 75, 100][i % 3]
                p.payment_usd = [600, 300, 0][i % 3]
        counts[status] += 1

    db.commit()
    total = len(pipelines)
    db.close()
    print(
        f"Updated {total} pipelines → "
        f"{counts['onboarded']} onboarded, {counts['rejected']} rejected, "
        f"{counts['in_progress']} in progress."
    )


if __name__ == "__main__":
    run()
