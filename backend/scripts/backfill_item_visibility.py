"""One-off backfill: sync every profile item's ``visibility`` to its section.

``update_share`` (app/profile/services.py) cascades a section's audience
level onto its items, but only on the next *change* — profiles that were
already published before that cascade shipped are stuck with items still at
their old ``visibility`` (usually the "private" default) even though the
section says "public". This walks every ``ProfileShare`` row once and applies
the same cascade so already-published profiles catch up.

Run:  docker compose exec backend python -m scripts.backfill_item_visibility --dry-run
      docker compose exec backend python -m scripts.backfill_item_visibility
"""

import argparse

from sqlalchemy import select

from app.core.database import SessionLocal
from app.profile.models import ProfileShare
from app.profile.services import _SECTION_ITEM_MODELS, _normalise_section_config


def main(dry_run: bool) -> None:
    db = SessionLocal()
    try:
        shares = list(db.execute(select(ProfileShare)).scalars())
        total_changed = 0
        for share in shares:
            for section, model in _SECTION_ITEM_MODELS.items():
                level = _normalise_section_config((share.sections or {}).get(section))[
                    "visibility"
                ]
                items = list(
                    db.execute(
                        select(model).where(model.user_id == share.user_id)
                    ).scalars()
                )
                for item in items:
                    if item.visibility != level:
                        print(
                            f"  user={share.user_id} {model.__tablename__}"
                            f"[{item.id}]: {item.visibility!r} -> {level!r}"
                        )
                        total_changed += 1
                        if not dry_run:
                            item.visibility = level

        if dry_run:
            print(
                f"\n{total_changed} item(s) would be updated (dry run, nothing written)."
            )
        else:
            db.commit()
            print(f"\nDone. {total_changed} item(s) updated.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
