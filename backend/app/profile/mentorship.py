"""Resolving a student's assigned mentor from hstaff.

hstaff owns the mentor↔mentee relationship; we hold no local mentorship table
(the ``mentorship:*`` permissions in identity.rbac_catalog are declared but
unbacked). So when a student submits a profile item for verification we ask
hstaff who their mentor is and route the submission to that person.

The routes are configured, not hardcoded, because they differ per deployment.
Per HSTAFF_API.md §Mentorship the live ones are:

    HSTAFF_MENTOR_PATH=/mentorship/my-mentor
    HSTAFF_MENTEES_PATH=/mentorship/my-mentees

Both are **self-scoped**: they describe the caller, not a user named in the
path. That is why every request here goes out under the subject's own token
(``_fetch_as_user``) and never the shared service account — the token is the
query. The templates still accept ``{username}``/``{email}``/``{user_id}`` so a
deployment with per-user routes can be pointed at them instead, but with the
self-scoped routes above the placeholders are simply unused.

The response parser below is deliberately tolerant: hstaff variants return the
mentor as a nested object, a flat set of ``mentor_*`` fields, or a list of
mentorships. Whatever the shape, we only need enough to identify the mentor —
an email or an hstaff user id — which is then matched to a local ``User`` row.
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.hstaff import client as hstaff
from app.identity.enums import UserSource
from app.identity.models import User

logger = logging.getLogger(__name__)

# Keys that may carry the mentor's email / hstaff id, in preference order.
_EMAIL_KEYS = ("email", "mentor_email", "user_email", "mentor_user_email")
_ID_KEYS = ("id", "user_id", "mentor_id", "mentor_user_id", "hstaff_user_id")
# Keys whose value may be a nested mentor object (or a list of them).
_NEST_KEYS = ("mentor", "mentor_user", "mentors", "results", "data", "mentorships")
# Keys that may carry a mentee's email in a mentees payload.
_MENTEE_EMAIL_KEYS = (
    "email",
    "mentee_email",
    "user_email",
    "talent_email",
    "candidate_email",
)
# Keys whose value may be a nested mentee object or a list of mentees.
_MENTEE_NEST_KEYS = (
    "mentees",
    "mentee",
    "results",
    "data",
    "items",
    "users",
    "talents",
    "talent",
    "candidates",
    "candidate",
)


def _first_str(source: dict, keys) -> str:
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _first_int(source: dict, keys) -> int | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def extract_mentor_identity(payload, _depth: int = 0) -> dict | None:
    """Pull ``{"email": ..., "hstaff_user_id": ...}`` out of an hstaff response.

    Returns None when the payload carries no usable identity — the caller then
    leaves the submission unassigned rather than guessing.
    """
    if payload is None or _depth > 4:
        return None

    if isinstance(payload, list):
        for entry in payload:
            found = extract_mentor_identity(entry, _depth + 1)
            if found:
                return found
        return None

    if not isinstance(payload, dict):
        return None

    email = _first_str(payload, _EMAIL_KEYS)
    hstaff_id = _first_int(payload, _ID_KEYS)
    if email or hstaff_id is not None:
        return {"email": email.lower(), "hstaff_user_id": hstaff_id}

    # Nothing at this level — descend into a nested mentor object / list.
    for key in _NEST_KEYS:
        if key in payload:
            found = extract_mentor_identity(payload[key], _depth + 1)
            if found:
                return found
    return None


def _local_user_for(db: Session, identity: dict) -> User | None:
    """Match an hstaff identity to a local mirrored user row."""
    hstaff_id = identity.get("hstaff_user_id")
    if hstaff_id is not None:
        user = db.execute(
            select(User).where(User.hstaff_user_id == hstaff_id)
        ).scalar_one_or_none()
        if user:
            return user
    email = (identity.get("email") or "").strip()
    if email:
        return db.execute(
            select(User).where(User.email.ilike(email))
        ).scalar_one_or_none()
    return None


def _fetch_as_user(db: Session, user: User, path: str):
    """GET ``path`` from hstaff **as this user**, never as the service account.

    Both mentorship routes we use are self-scoped — ``/mentorship/my-mentor``
    and ``/mentorship/my-mentees`` answer "who mentors *the caller*". So the
    identity of the token *is* the query: asking with the shared HR service
    token would silently return that account's mentorship and attribute it to
    whoever we were asking about. There is deliberately no service-account
    fallback here; a user we can't authenticate as is reported as unknown.

    On 401 we rotate the stored refresh token exactly as
    ``hstaff.router._refresh_token`` does and retry once.
    """
    token = user.hstaff_access_token or ""
    if not token:
        return 401, None
    status, body = hstaff.client.get(token, path)
    if status == 401 and _refresh_user_token(db, user):
        status, body = hstaff.client.get(user.hstaff_access_token, path)
    return status, body


def _refresh_user_token(db: Session, user: User) -> bool:
    """Rotate the student's hstaff tokens in place. hstaff's refresh tokens are
    single-use, so the replacement must be persisted or the account is locked
    out of passthrough until its next password login."""
    if not user.hstaff_refresh_token:
        return False
    try:
        data = hstaff.client.refresh(user.hstaff_refresh_token)
    except hstaff.HstaffError:
        return False
    user.hstaff_access_token = data.get("access_token", user.hstaff_access_token)
    user.hstaff_refresh_token = data.get("refresh_token", user.hstaff_refresh_token)
    db.commit()
    return True


def _username_for(student: User) -> str:
    """hstaff usernames mirror the email local-part (see spec §4.3 slugs)."""
    return (student.email or "").split("@")[0]


def _mentor_from_mirror(db: Session, student: User) -> User | None:
    """Fall back to the local ``MenteeRecord`` mirror of hstaff's relationship.

    The mirror is written at the mentor's login (``identity.services
    .sync_mentor_role``) from the very same hstaff data, so this is not a guess
    — it's the last answer hstaff gave us. It keeps submissions routed while
    hstaff is briefly unreachable. A mentee listed under several mentors is left
    unassigned rather than routed arbitrarily.
    """
    from app.identity.models import MenteeRecord

    email = (student.email or "").strip().lower()
    if not email:
        return None
    mentor_ids = [
        row[0]
        for row in db.execute(
            select(MenteeRecord.mentor_id).where(MenteeRecord.mentee_email == email)
        )
    ]
    if len(mentor_ids) != 1:
        return None
    return db.get(User, mentor_ids[0])


def lookup_mentor(db: Session, student: User) -> User | None:
    """Ask hstaff for this student's mentor and map them to a local user.

    hstaff owns the relationship; when it can't answer we fall back to the local
    mirror of what it last told us (``_mentor_from_mirror``). Returns None
    (never raises) when neither source knows, or when the mentor isn't mirrored
    locally. Callers treat that as "unassigned" so a submission is never lost —
    see ``services.review_queue``.
    """
    path_template = settings.HSTAFF_MENTOR_PATH
    if not path_template or not settings.HSTAFF_ENABLED:
        return _mentor_from_mirror(db, student)
    if student.source != UserSource.HSTAFF.value or not student.hstaff_user_id:
        # Local-only accounts have no hstaff mentorship record to consult.
        return _mentor_from_mirror(db, student)

    path = path_template.format(
        username=_username_for(student),
        email=student.email or "",
        user_id=student.hstaff_user_id,
    )
    try:
        status, body = _fetch_as_user(db, student, path)
    except hstaff.HstaffError as exc:
        logger.warning("hstaff mentor lookup failed for %s: %s", student.email, exc)
        return _mentor_from_mirror(db, student)
    if status != 200:
        logger.info("hstaff mentor lookup for %s returned %s", student.email, status)
        return _mentor_from_mirror(db, student)

    identity = extract_mentor_identity(body)
    if not identity:
        logger.info("hstaff returned no mentor for %s", student.email)
        return None  # hstaff answered: no mentor. Don't resurrect a stale one.

    mentor = _local_user_for(db, identity)
    if mentor is not None:
        record_relationship(db, mentor, student)
    if mentor is None:
        logger.info(
            "mentor %s for %s is not mirrored locally yet",
            identity.get("email") or identity.get("hstaff_user_id"),
            student.email,
        )
    return mentor


def extract_assignment_pairs(payload, _depth: int = 0) -> list[tuple[dict, str]]:
    """Pull ``(mentor_identity, mentee_email)`` pairs from ``/mentorship/assignments``.

    This is the one *global* mentorship route (admin-scoped), so unlike the
    self-scoped lookups each entry has to name both sides. We read the mentor
    from a ``mentor``-ish key and the mentee from a ``mentee``-ish key; entries
    missing either side are skipped rather than guessed at, because a half-read
    assignment would silently mis-route someone's submissions.
    """
    if payload is None or _depth > 4:
        return []

    if isinstance(payload, list):
        pairs: list[tuple[dict, str]] = []
        for entry in payload:
            pairs.extend(extract_assignment_pairs(entry, _depth + 1))
        return pairs

    if not isinstance(payload, dict):
        return []

    mentor_blob = None
    for key in ("mentor", "mentor_user", "mentor_detail"):
        if isinstance(payload.get(key), dict):
            mentor_blob = payload[key]
            break
    mentee_blob = None
    for key in ("mentee", "mentee_user", "talent", "user", "mentee_detail"):
        if isinstance(payload.get(key), dict):
            mentee_blob = payload[key]
            break

    if mentor_blob is not None and mentee_blob is not None:
        mentor = extract_mentor_identity(mentor_blob, _depth + 1)
        mentee_email = _first_str(mentee_blob, _MENTEE_EMAIL_KEYS).lower()
        if mentor and mentee_email:
            return [(mentor, mentee_email)]
        return []

    # Flat shape: mentor_email/mentee_email on the row itself.
    flat_mentor_email = _first_str(payload, ("mentor_email", "mentor_user_email"))
    flat_mentee_email = _first_str(payload, ("mentee_email", "talent_email"))
    if flat_mentor_email and flat_mentee_email:
        return [
            (
                {
                    "email": flat_mentor_email.lower(),
                    "hstaff_user_id": _first_int(payload, ("mentor_id",)),
                },
                flat_mentee_email.lower(),
            )
        ]

    # Otherwise descend into a paginated / wrapped list.
    for key in ("results", "data", "items", "assignments", "mentorships"):
        if key in payload:
            found = extract_assignment_pairs(payload[key], _depth + 1)
            if found:
                return found
    return []


def fetch_all_assignments(db: Session) -> list[tuple[User, str]] | None:
    """Every mentor↔mentee pair hstaff knows, as ``(local mentor, mentee email)``.

    Uses the admin-scoped ``/mentorship/assignments`` via the service account —
    the one route that is legitimately global rather than self-scoped. Returns
    None when hstaff can't be asked (so callers leave the mirror alone) and []
    when it answers with nothing usable. Mentors we don't mirror locally are
    dropped with a log line.
    """
    path = settings.HSTAFF_ASSIGNMENTS_PATH
    if not path or not settings.HSTAFF_ENABLED:
        return None
    try:
        status, body = hstaff.client.service_get(path)
    except hstaff.HstaffError as exc:
        logger.warning("hstaff assignments fetch failed: %s", exc)
        return None
    if status != 200:
        logger.warning("hstaff assignments fetch returned %s", status)
        return None

    out: list[tuple[User, str]] = []
    for identity, mentee_email in extract_assignment_pairs(body):
        mentor = _local_user_for(db, identity)
        if mentor is None:
            logger.info(
                "assignment mentor %s not mirrored locally; skipped",
                identity.get("email") or identity.get("hstaff_user_id"),
            )
            continue
        out.append((mentor, mentee_email))
    return out


def record_relationship(db: Session, mentor: User, student: User) -> None:
    """Persist a mentor↔mentee pair hstaff just confirmed, and grant the role.

    Called when a submission resolves a mentor, so the mirror fills in from the
    submit side without waiting for the mentor's own login or a bulk backfill.
    hstaff remains the source of truth — this only writes down what it just
    told us, and the login sync still reconciles (including deletions) later.

    The mentor role is granted here because a mentor who has never logged in
    since the feature shipped would otherwise be routed submissions they lack
    the permission to open.
    """
    from app.identity.models import MenteeRecord
    from app.identity.services import _grant_mentor_role

    email = (student.email or "").strip().lower()
    if not email:
        return
    exists = db.execute(
        select(MenteeRecord.id).where(
            MenteeRecord.mentor_id == mentor.id,
            MenteeRecord.mentee_email == email,
        )
    ).first()
    if not exists:
        db.add(MenteeRecord(mentor_id=mentor.id, mentee_email=email))
    _grant_mentor_role(db, mentor)
    db.flush()


# ---------- mentees (reverse lookup) ----------
def extract_mentee_emails(payload, _depth: int = 0) -> list[str]:
    """Pull every mentee email out of an hstaff mentees response.

    hstaff variants return a flat list of mentee objects, a nested list under a
    key, or a single mentee object. We walk everything, deduplicate, and return
    lower-cased emails. Empty result when the payload carries no usable email —
    the caller then leaves the local mirror unchanged rather than guessing.
    """
    if payload is None or _depth > 4:
        return []

    if isinstance(payload, list):
        found: list[str] = []
        for entry in payload:
            found.extend(extract_mentee_emails(entry, _depth + 1))
        return _dedupe(found)

    if not isinstance(payload, dict):
        return []

    email = _first_str(payload, _MENTEE_EMAIL_KEYS)
    if email:
        return [email.lower()]

    # Nothing at this level — descend into nested mentee object / list.
    for key in _MENTEE_NEST_KEYS:
        if key in payload:
            found = extract_mentee_emails(payload[key], _depth + 1)
            if found:
                return found
    return []


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        item = item.strip().lower()
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def lookup_mentees(db: Session, mentor: User) -> list[str] | None:
    """Ask hstaff for the users this person mentors and return their emails.

    Never raises. The two "empty" cases are deliberately distinguished, because
    the caller reconciles the local mirror against this answer:

    * ``None``  — we could not ask (hstaff disabled/unreachable/unconfigured, or
      a local-only account). hstaff's answer is unknown, so the caller keeps the
      existing ``MenteeRecord`` mirror as-is.
    * ``[]``    — hstaff answered and this user mentors nobody. The caller drops
      the mirrored rows, so the local relationship follows hstaff.
    """
    path_template = settings.HSTAFF_MENTEES_PATH
    if not path_template or not settings.HSTAFF_ENABLED:
        return None
    if mentor.source != UserSource.HSTAFF.value or not mentor.hstaff_user_id:
        # Local-only accounts have no hstaff mentorship record to consult.
        return None

    path = path_template.format(
        username=_username_for(mentor),
        email=mentor.email or "",
        user_id=mentor.hstaff_user_id,
    )
    try:
        status, body = _fetch_as_user(db, mentor, path)
    except hstaff.HstaffError as exc:
        logger.warning("hstaff mentees lookup failed for %s: %s", mentor.email, exc)
        return None
    if status != 200:
        logger.info("hstaff mentees lookup for %s returned %s", mentor.email, status)
        return None

    emails = extract_mentee_emails(body)
    if not emails:
        logger.info("hstaff returned no mentees for %s", mentor.email)
    return emails
