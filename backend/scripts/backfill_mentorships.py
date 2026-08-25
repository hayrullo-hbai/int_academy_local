"""Backfill every mentor↔mentee relationship from hstaff into ``MenteeRecord``.

Login only syncs the person logging in, and it does so under *their* token — so
a mentor who hasn't signed in since the feature shipped has no mentee list
locally, and their mentees' submissions fall to the shared fallback queue. This
seeds the whole mirror in one pass; from then on
``identity.services.sync_mentor_role`` keeps each user fresh at their login.

It reads ``/mentorship/assignments`` (HSTAFF_ASSIGNMENTS_PATH), the one
admin-scoped mentorship route — the per-user ``my-mentor``/``my-mentees`` routes
are self-scoped and answer only for the caller, so they can't be used to
enumerate other people. Being admin-scoped, this one runs under the service
account, which is exactly why the credentials must be valid.

hstaff stays authoritative: every pair it lists is written, every mirrored pair
it no longer lists is deleted, and mentors are granted the local ``mentor`` role
as a regular role without touching anyone's primary role. If hstaff can't be
reached the mirror is left untouched rather than emptied.

Requires HSTAFF_ASSIGNMENTS_PATH and valid HSTAFF_SERVICE_* credentials; both
are checked up front so a failure can't silently record "nobody mentors anyone"
across the whole company.

Run:  docker compose exec backend python -m scripts.backfill_mentorships --dry-run
      docker compose exec backend python -m scripts.backfill_mentorships
"""

import argparse
import logging
import sys

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.hstaff import client as hstaff
from app.identity.models import MenteeRecord
from app.identity.services import _grant_mentor_role
from app.profile.mentorship import fetch_all_assignments

logger = logging.getLogger("backfill_mentorships")


def _preflight() -> str | None:
    """Return an error message if we can't trust what hstaff would tell us."""
    if not settings.HSTAFF_ENABLED:
        return "HSTAFF_ENABLED is false — nothing to pull from."
    if not settings.HSTAFF_ASSIGNMENTS_PATH:
        return (
            "HSTAFF_ASSIGNMENTS_PATH is not set. It should be the admin-scoped "
            "route listing every mentorship, per HSTAFF_API.md:\n"
            "    HSTAFF_ASSIGNMENTS_PATH=/mentorship/assignments"
        )
    try:
        hstaff.client._service_token(force=True)
    except hstaff.HstaffAuthError:
        return (
            "hstaff rejected the service account (401). Check/rotate "
            "HSTAFF_SERVICE_EMAIL / HSTAFF_SERVICE_PASSWORD — /mentorship/"
            "assignments is admin-scoped, so without a valid service token it "
            "returns nothing and the backfill would wipe the mirror."
        )
    except hstaff.HstaffError as exc:
        return f"Could not reach hstaff: {exc}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing (rolls back at the end).",
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="Only process the first N users."
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    problem = _preflight()
    if problem:
        print(f"Refusing to run:\n{problem}", file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        pairs = fetch_all_assignments(db)
        if pairs is None:
            print(
                "hstaff could not be asked for assignments — mirror left "
                "untouched. See the log line above for why.",
                file=sys.stderr,
            )
            return 1
        if args.limit:
            pairs = pairs[: args.limit]

        before = db.execute(select(MenteeRecord)).scalars().all()
        pairs_before = {(str(r.mentor_id), r.mentee_email) for r in before}

        # hstaff is authoritative: everything it lists is written, and anything
        # mirrored locally that it no longer lists is dropped.
        wanted = {(str(m.id), email) for m, email in pairs}
        for mentor, email in pairs:
            if (str(mentor.id), email) not in pairs_before:
                db.add(MenteeRecord(mentor_id=mentor.id, mentee_email=email))
            _grant_mentor_role(db, mentor)
        for row in before:
            if (str(row.mentor_id), row.mentee_email) not in wanted:
                db.delete(row)

        db.flush()
        after = db.execute(select(MenteeRecord)).scalars().all()
        pairs_after = {(str(r.mentor_id), r.mentee_email) for r in after}
        mentors = len({mentor_id for mentor_id, _ in pairs_after})

        added = pairs_after - pairs_before
        removed = pairs_before - pairs_after
        print(
            f"\n{len(pairs)} assignments returned by hstaff.\n"
            f"  mentorship pairs: {len(pairs_before)} -> {len(pairs_after)} "
            f"(+{len(added)}, -{len(removed)})\n"
            f"  distinct mentors on file: {mentors}"
        )
        for mentor_id, email in sorted(added):
            print(f"  + {email} -> mentor {mentor_id}")
        for mentor_id, email in sorted(removed):
            print(f"  - {email} (no longer mentored by {mentor_id})")

        if args.dry_run:
            db.rollback()
            print("\nDry run — rolled back, nothing written.")
        else:
            db.commit()
            print("\nCommitted.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
