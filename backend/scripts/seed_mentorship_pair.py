"""Create a local student + mentor pair for exercising the review flow.

Local (source=LOCAL) accounts authenticate against our own password hash and
never touch hstaff, so this works while the hstaff service credentials are
down. It also links the two with a ``MenteeRecord`` row, which is what makes
the pair actually useful: ``mentorship.lookup_mentor`` falls back to that mirror
for accounts it can't ask hstaff about, so a submission by the student routes to
the mentor and shows up in their review queue end-to-end.

Idempotent: re-running resets the passwords and re-links the pair rather than
creating duplicates.

Run:  docker compose exec backend python -m scripts.seed_mentorship_pair
"""

import logging

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.identity.enums import UserSource, UserStatus
from app.identity.models import MenteeRecord, Role, User

logger = logging.getLogger("seed_mentorship_pair")

PASSWORD = "password"  # pragma: allowlist secret
STUDENT_EMAIL = "student@example.com"
MENTOR_EMAIL = "mentor@example.com"


def _upsert_local_user(db, email: str, full_name: str, role_name: str) -> User:
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        user = User(email=email, source=UserSource.LOCAL.value)
        db.add(user)
        db.flush()
    user.full_name = full_name
    user.source = UserSource.LOCAL.value
    user.password_hash = hash_password(PASSWORD)
    user.is_active = True
    user.is_verified = True
    user.status = UserStatus.ACTIVE.value
    db.flush()

    role = db.execute(select(Role).where(Role.name == role_name)).scalar_one_or_none()
    if role is None:
        logger.warning(
            "role %r not found — run the RBAC seed first; %s left without it",
            role_name,
            email,
        )
    else:
        user.role = role  # primary
        user.roles = [role]
    db.flush()
    return user


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    db = SessionLocal()
    try:
        # "foundation" is a learner role (identity.enums.LEARNER_ROLES); "mentor"
        # is what profile.access.VERIFIER_ROLES checks for review permission.
        student = _upsert_local_user(db, STUDENT_EMAIL, "Test Student", "foundation")
        mentor = _upsert_local_user(db, MENTOR_EMAIL, "Test Mentor", "mentor")

        exists = db.execute(
            select(MenteeRecord.id).where(
                MenteeRecord.mentor_id == mentor.id,
                MenteeRecord.mentee_email == STUDENT_EMAIL,
            )
        ).first()
        if not exists:
            db.add(MenteeRecord(mentor_id=mentor.id, mentee_email=STUDENT_EMAIL))
        db.commit()

        print(
            f"student: {STUDENT_EMAIL} / {PASSWORD}  (role: {student.role_name})\n"
            f"mentor:  {MENTOR_EMAIL} / {PASSWORD}  (role: {mentor.role_name})\n"
            f"linked:  {STUDENT_EMAIL} -> {MENTOR_EMAIL} via mentee_records"
        )
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
