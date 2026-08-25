"""Small cross-cutting helpers that don't belong to a single domain."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session


def resolve_user_by_ident(db: Session, model, ident: str):
    """Look up a user by full email or by the local-part slug.

    Examples:
      - ``john@example.com`` matches exactly (case-insensitive).
      - ``john`` matches the first email that starts with ``john@``.

    ``%`` and ``_`` in the ident are escaped so they can never act as SQL LIKE
    wildcards. Any database error degrades to ``None`` instead of propagating.
    """
    ident = (ident or "").strip()
    if not ident:
        return None
    try:
        user = db.execute(
            select(model).where(func.lower(model.email) == ident.lower())
        ).scalar_one_or_none()
        if user is not None or "@" in ident:
            return user
        prefix = (
            ident.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        return (
            db.execute(
                select(model).where(
                    func.lower(model.email).startswith(prefix + "@", escape="\\")
                )
            )
            .scalars()
            .first()
        )
    except Exception:
        return None
