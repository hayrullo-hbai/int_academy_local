import pytest

from app.profile import access, services
from app.profile.enums import ProfileSection


@pytest.fixture
def share(db, users):
    return services.get_share(db, users["owner"].id)


@pytest.fixture
def approved_project(db, users):
    # No explicit visibility: items are created Private by default, and only
    # a curator may set visibility at all (access.can_set_visibility) — even
    # to "private" — so the owner must omit it rather than pick it.
    p = services.create_project(
        db,
        users["owner"].id,
        {"title": "P", "kind": "external"},
        users["owner"],
    )
    p.review_state = "verified"
    p.approved_snapshot = p.snapshot()
    p.approved_version = p.version
    db.commit()
    return p


def _publish(db, share, *sections):
    share.is_published = True
    share.sections = {s: True for s in sections}
    db.commit()


@pytest.mark.parametrize(
    "viewer, published, section_on, expected",
    [
        (None, False, False, False),
        (None, False, True, False),
        (None, True, False, False),
        (None, True, True, True),
        ("stranger", False, False, True),
        ("stranger", True, True, True),
        ("hr", True, False, True),
        ("owner", True, False, True),
        ("manager", True, False, True),
        ("mentor", True, False, True),
    ],
)
def test_can_view_section_matrix(
    db, users, share, viewer, published, section_on, expected
):
    share.is_published = published
    share.sections = {"skills": section_on}
    db.commit()
    viewer = users[viewer] if viewer else None
    assert (
        access.can_view_section(viewer, share, ProfileSection.SKILLS.value) == expected
    )


@pytest.mark.parametrize("viewer", ["stranger", "hr", "mentor", "manager", "admin"])
def test_logged_in_users_see_approved_private_item(db, users, approved_project, viewer):
    # "private" is logged-in-only: everyone logged in sees it, anonymous does not.
    assert access.can_view_item(users[viewer], users["owner"].id, approved_project)


def test_anonymous_sees_approved_public_item(db, users):
    p = services.create_project(
        db,
        users["owner"].id,
        {"title": "Pub", "kind": "external", "visibility": "public"},
        users["manager"],
    )
    p.review_state = "verified"
    p.approved_snapshot = p.snapshot()
    p.approved_version = p.version
    db.commit()
    assert access.can_view_item(None, users["owner"].id, p)


def test_anonymous_does_not_see_private_or_restricted_item(db, users, approved_project):
    assert not access.can_view_item(None, users["owner"].id, approved_project)


def _approved(db, users, owner_id, visibility):
    # Only "public" needs an explicit curator-set value; "private" is the
    # default an owner gets by omitting visibility entirely (see
    # approved_project above — owners can no longer set it themselves).
    body = {"title": f"V-{visibility}", "kind": "external"}
    requester = users["owner"]
    if visibility == "public":
        body["visibility"] = "public"
        requester = users["manager"]
    p = services.create_project(db, owner_id, body, requester)
    p.review_state = "verified"
    p.approved_snapshot = p.snapshot()
    p.approved_version = p.version
    db.commit()
    return p


@pytest.mark.parametrize(
    "item_level, viewer, expected",
    [
        # "private" is logged-in-only: any authenticated role admits.
        ("private", "stranger", True),
        ("private", "hr", True),
        ("private", "mentor", True),
        ("private", None, False),
        # "public" is what anonymous visitors may also see.
        ("public", "stranger", True),
        ("public", None, True),
    ],
)
def test_visibility_levels_gate_viewers(db, users, item_level, viewer, expected):
    item = _approved(db, users, users["owner"].id, item_level)
    v = users[viewer] if viewer else None
    assert access.can_view_item(v, users["owner"].id, item) is expected


def test_curator_and_owner_see_any_visibility_level(db, users):
    for level in ["private", "public"]:
        item = _approved(db, users, users["owner"].id, level)
        assert access.can_view_item(users["owner"], users["owner"].id, item)
        assert access.can_view_item(users["manager"], users["owner"].id, item)
        assert access.can_view_item(users["admin"], users["owner"].id, item)


def test_curator_and_owner_see_draft_item(db, users):
    p = services.create_project(
        db, users["owner"].id, {"title": "Draft", "kind": "external"}, users["owner"]
    )
    assert access.can_view_item(users["owner"], users["owner"].id, p)
    assert access.can_view_item(users["manager"], users["owner"].id, p)


def test_mentor_hr_do_not_see_draft_item(db, users):
    p = services.create_project(
        db, users["owner"].id, {"title": "Draft", "kind": "external"}, users["owner"]
    )
    assert not access.can_view_item(users["mentor"], users["owner"].id, p)
    assert not access.can_view_item(users["hr"], users["owner"].id, p)


def test_shows_stays_boolean_contract(db, users, share):
    _publish(db, share, "skills")
    assert isinstance(share.shows("skills"), bool)
    assert share.shows("skills") is True
    assert share.shows("projects") is False


# ---- v2: per-section audiences + curator's unverified option -----------------
# NOTE: the "include_unverified" opt-in was removed — public shares only
# expose verified items, so the tests below are commented out.


# def _section_share(db, users, visibility, *, published=True, include_unverified=False):
def _section_share(db, users, visibility, *, published=True):
    share = services.get_share(db, users["owner"].id)
    share.is_published = published
    share.sections = {"skills": {"visibility": visibility}}
    db.commit()
    return share


@pytest.mark.parametrize(
    "level, published, viewer, expected",
    [
        # private admits every logged-in user — not anonymous.
        ("private", True, "stranger", True),
        ("private", True, "hr", True),
        ("private", True, "mentor", True),
        ("private", True, None, False),
        # public opens the section to anonymous — but only when published.
        ("public", True, None, True),
        ("public", False, None, False),
        ("public", False, "stranger", True),  # logged-in unaffected by publication
    ],
)
def test_section_audience_levels(db, users, level, published, viewer, expected):
    share = _section_share(db, users, level, published=published)
    v = users[viewer] if viewer else None
    assert access.can_view_section(v, share, ProfileSection.SKILLS.value) is expected


def test_private_section_closes_anonymous_even_when_published(db, users):
    share = _section_share(db, users, "private", published=True)
    assert not access.can_view_section(None, share, ProfileSection.SKILLS.value)
    assert access.can_view_section(
        users["stranger"], share, ProfileSection.SKILLS.value
    )


# def test_unverified_items_visible_when_section_opts_in(db, users):
#     draft = services.create_project(
#         db, users["owner"].id, {"title": "Draft", "kind": "external"}, users["owner"]
#     )
#     share = _section_share(db, users, "public", include_unverified=True)
#     assert access.can_view_item(
#         None, users["owner"].id, draft, section_unverified=share.include_unverified("skills")
#     )
#     assert access.can_view_item(
#         users["stranger"], users["owner"].id, draft,
#         section_unverified=share.include_unverified("skills"),
#     )


# def test_unverified_items_hidden_without_section_opt_in(db, users):
#     draft = services.create_project(
#         db, users["owner"].id, {"title": "Draft", "kind": "external"}, users["owner"]
#     )
#     share = _section_share(db, users, "public", include_unverified=False)
#     assert not access.can_view_item(
#         users["stranger"], users["owner"].id, draft,
#         section_unverified=share.include_unverified("skills"),
#     )


# def test_rejected_item_never_leaks_through_unverified_opt_in(db, users):
#     draft = services.create_project(
#         db, users["owner"].id, {"title": "Draft", "kind": "external"}, users["owner"]
#     )
#     draft.review_state = "rejected"
#     db.commit()
#     share = _section_share(db, users, "public", include_unverified=True)
#     assert not access.can_view_item(
#         users["stranger"], users["owner"].id, draft,
#         section_unverified=share.include_unverified("skills"),
#     )


def test_update_share_normalises_legacy_and_new_section_configs(db, users):
    share = services.get_share(db, users["owner"].id)
    share.sections = {"skills": True, "projects": {"visibility": "public"}}
    db.commit()
    assert share.section_visibility("skills") == "public"
    assert share.section_visibility("projects") == "public"
    # assert share.include_unverified("projects") is True
    # assert share.include_unverified("skills") is False

    share = services.update_share(
        db,
        users["owner"].id,
        users["manager"],
        sections={"projects": {"visibility": "private"}},
        reason="switch audience",
    )
    assert share.section_visibility("projects") == "private"
    # assert share.include_unverified("projects") is False
    assert share.section_visibility("skills") == "public"


def test_update_share_rejects_unknown_section_and_non_curator(db, users):
    with pytest.raises(ValueError):
        services.update_share(
            db,
            users["owner"].id,
            users["manager"],
            sections={"nope": {"visibility": "public"}},
            reason="x",
        )
    with pytest.raises(PermissionError):
        services.update_share(
            db,
            users["owner"].id,
            users["owner"],
            sections={"skills": {"visibility": "public"}},
            reason="x",
        )
