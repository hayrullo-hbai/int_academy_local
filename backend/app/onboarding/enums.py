import enum


class OnboardingStatus(str, enum.Enum):
    IN_PROGRESS = "in_progress"
    REJECTED = "rejected"
    ONBOARDED = "onboarded"


class StageOutcome(str, enum.Enum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


class ChatKind(str, enum.Enum):
    CANDIDATE = "candidate"  # candidate + interviewers + management
    DISCUSSION = "discussion"  # interviewers + management (no candidate)


class StageKind(str, enum.Enum):
    INTERVIEW = "interview"  # zoom + interviewer report + pass/fail
    DISCUSSION = "discussion"  # zoom + report + pass/fail (converges 1-3)
    PAYMENT = "payment"  # candidate uploads proof + management approval
    ACCESS = "access"  # zoom + staff completes -> enters school tier


ONBOARDING_STATUS_VALUES = {s.value for s in OnboardingStatus}

# Ordered pipeline definition (mirrors the diagram: 1-3 parallel, then 4->5->6).
STAGE_DEFS = [
    {
        "key": "intro_call",
        "num": 1,
        "label": "5–15 min call",
        "kind": StageKind.INTERVIEW.value,
        "zoom": True,
    },
    {
        "key": "tech_interview",
        "num": 2,
        "label": "Tech interview",
        "kind": StageKind.INTERVIEW.value,
        "zoom": True,
    },
    {
        "key": "culture_fit",
        "num": 3,
        "label": "Culture-fit interview",
        "kind": StageKind.INTERVIEW.value,
        "zoom": True,
    },
    {
        "key": "discussion",
        "num": 4,
        "label": "Interviewer discussion",
        "kind": StageKind.DISCUSSION.value,
        "zoom": True,
    },
    {
        "key": "payment",
        "num": 5,
        "label": "Payment",
        "kind": StageKind.PAYMENT.value,
        "zoom": False,
    },
    {
        "key": "access",
        "num": 6,
        "label": "Platforms & docs access",
        "kind": StageKind.ACCESS.value,
        "zoom": True,
    },
]

STAGE_BY_KEY = {d["key"]: d for d in STAGE_DEFS}
STAGE_KEYS = [d["key"] for d in STAGE_DEFS]

INTERVIEW_KEYS = [
    "intro_call",
    "tech_interview",
    "culture_fit",
]  # the three parallel stages
REPORT_KEYS = INTERVIEW_KEYS + ["discussion"]  # stages needing a written report
ZOOM_KEYS = [d["key"] for d in STAGE_DEFS if d["zoom"]]  # 1,2,3,4,6
