import pytest

from app.profile import services


def _project(db, user):
    return services.create_project(db, user.id, {"title": "T"}, user)


def test_owner_creates_project_without_visibility(db, users):
    p = _project(db, users["owner"])
    assert p.title == "T"
    assert p.review_state == "draft"


def test_owner_cannot_set_own_visibility(db, users):
    # Visibility is curated, not self-declared (access.can_set_visibility):
    # only Academy Manager / Admin may set it, at every level — even "private".
    with pytest.raises(PermissionError, match="Academy Manager"):
        services.create_project(
            db,
            users["owner"].id,
            {"title": "T", "visibility": "private"},
            users["owner"],
        )


def test_owner_cannot_set_public_level(db, users):
    with pytest.raises(PermissionError, match="Academy Manager"):
        services.create_project(
            db,
            users["owner"].id,
            {"title": "T", "visibility": "public"},
            users["owner"],
        )


def test_academy_manager_and_admin_can_publish(db, users):
    for who in (users["manager"], users["admin"]):
        p = services.create_project(
            db,
            users["owner"].id,
            {"title": "T", "visibility": "public"},
            who,
        )
        assert p.visibility == "public"


def test_update_project_visibility_no_crash(db, users):
    p = _project(db, users["owner"])
    services.update_project(
        db,
        p,
        {"visibility": "private"},
        users["manager"],
    )
    assert p.visibility == "private"


def test_create_rejects_unknown_level(db, users):
    with pytest.raises(ValueError, match="Unknown visibility"):
        services.create_project(
            db,
            users["owner"].id,
            {"title": "T", "visibility": "secret"},
            users["owner"],
        )


def test_hr_cannot_publish_item(db, users):
    with pytest.raises(PermissionError):
        services.create_project(
            db,
            users["owner"].id,
            {"title": "T", "visibility": "public"},
            users["hr"],
        )


# ---------------------------------------------------------------------------
# Linked skills (skill_ids) — replaced the free-text `technologies` list.
# ---------------------------------------------------------------------------
def _skill(db, user, name="Python"):
    return services.create_skill(
        db, user.id, {"name": name, "category": "programming_languages"}, user
    )


def test_project_links_own_skill(db, users):
    skill = _skill(db, users["owner"])
    p = services.create_project(
        db,
        users["owner"].id,
        {"title": "T", "skill_ids": [str(skill.id)]},
        users["owner"],
    )
    assert p.skill_ids == [str(skill.id)]


def test_project_cannot_link_someone_elses_skill(db, users):
    other = _skill(db, users["stranger"])
    with pytest.raises(ValueError, match="your own profile"):
        services.create_project(
            db,
            users["owner"].id,
            {"title": "T", "skill_ids": [str(other.id)]},
            users["owner"],
        )


def test_project_rejects_non_uuid_skill_link(db, users):
    with pytest.raises(ValueError, match="skill id"):
        services.create_project(
            db,
            users["owner"].id,
            {"title": "T", "skill_ids": ["python"]},
            users["owner"],
        )


def test_deleting_a_skill_unlinks_it_from_projects(db, users):
    keep, drop = _skill(db, users["owner"]), _skill(db, users["owner"], "Rust")
    p = services.create_project(
        db,
        users["owner"].id,
        {"title": "T", "skill_ids": [str(keep.id), str(drop.id)]},
        users["owner"],
    )
    services.delete_skill(db, drop, users["owner"])
    db.refresh(p)
    assert p.skill_ids == [str(keep.id)]


def test_non_owner_payload_hides_skill_ids_and_unverified_links(db, users):
    """The audience sees resolved, verified skills only — never the raw id
    list, which would leak the links `_linked_skills` withholds."""
    from app.profile import router

    verified, pending = _skill(db, users["owner"]), _skill(db, users["owner"], "Rust")
    verified.review_state, verified.approved_version = "verified", verified.version
    db.flush()
    p = services.create_project(
        db,
        users["owner"].id,
        {"title": "T", "skill_ids": [str(verified.id), str(pending.id)]},
        users["owner"],
    )

    public = router._build_project_payload(db, p, owner_view=False)
    assert "skill_ids" not in public
    assert [s["name"] for s in public["skills"]] == ["Python"]

    owner = router._build_project_payload(db, p, owner_view=True)
    assert owner["skill_ids"] == [str(verified.id), str(pending.id)]
    assert [s["name"] for s in owner["skills"]] == ["Python", "Rust"]
