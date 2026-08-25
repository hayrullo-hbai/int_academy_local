import pytest

from app.profile import services


def test_owner_cannot_set_own_visibility(db, users):
    # Visibility is curated, not self-declared (access.can_set_visibility):
    # only Academy Manager / Admin may set it, at every level — even "private".
    with pytest.raises(PermissionError, match="Academy Manager"):
        services.create_skill(
            db,
            users["owner"].id,
            {
                "name": "Python",
                "category": "programming_languages",
                "visibility": "private",
            },
            users["owner"],
        )


def test_owner_cannot_set_skill_public(db, users):
    with pytest.raises(PermissionError, match="Academy Manager"):
        services.create_skill(
            db,
            users["owner"].id,
            {"name": "Go", "category": "programming_languages", "visibility": "public"},
            users["owner"],
        )
