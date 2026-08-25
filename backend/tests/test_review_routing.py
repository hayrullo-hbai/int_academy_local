"""Tier-based routing of the verification workflow.

Junior tiers (school / softlanding / foundation / fundamental) are reviewed by
the mentor AND an academy manager; senior tiers (talent / growth3) by the
mentor alone; everyone else is auto-verified on submit. Admin sees everything.
No-role users can't edit their profile at all (see access.has_any_role), so
they never reach the review workflow in the first place.
"""

import pytest
from sqlalchemy import select

from app.identity.models import Role, User
from app.profile import services
from app.profile.enums import ReviewState


def _user(db, email, role_name, extra_roles=()):
    roles = []
    for name in (role_name, *extra_roles):
        role = db.execute(select(Role).where(Role.name == name)).scalar_one_or_none()
        if role is None:
            role = Role(name=name, display_name=name)
            db.add(role)
            db.flush()
        roles.append(role)
    user = User(
        email=email,
        full_name=email.split("@")[0],
        password_hash="x",
        roles=roles,
    )
    db.add(user)
    db.flush()
    return user


def _no_role_user(db, email):
    user = User(email=email, full_name=email.split("@")[0], password_hash="x")
    db.add(user)
    db.flush()
    return user


def _submit_skill(db, student, requester):
    skill = services.create_skill(
        db, student.id, {"name": "Python", "category": "programming_languages"}, student
    )
    record = services.submit_for_review(db, "skill", skill, requester)
    return skill, record


# ---- routing on submit -----------------------------------------------------
@pytest.mark.parametrize(
    "tier, expects_review, academy_manager_flag",
    [
        ("school", True, True),
        ("softlanding", True, True),
        ("foundation", True, True),
        ("fundamental", True, True),
        ("talent", True, False),
        ("growth3", True, False),
        ("mentor", False, False),
        ("hr", False, False),
        ("pm", False, False),
        ("examiner", False, False),
    ],
)
def test_submit_routes_by_tier(db, tier, expects_review, academy_manager_flag, users):
    student = _user(db, f"tier-{tier}@academy.test", tier)
    skill, record = _submit_skill(db, student, student)
    if expects_review:
        assert record is not None
        assert record.academy_manager_review is academy_manager_flag
        assert skill.review_state == ReviewState.SUBMITTED.value
    else:
        assert record is None
        assert skill.review_state == ReviewState.VERIFIED.value
        assert skill.approved_snapshot is not None


def test_talent_wins_over_foundation_for_dual_role_student(db, users):
    student = _user(db, "dual@academy.test", "foundation", extra_roles=("talent",))
    skill, record = _submit_skill(db, student, student)
    assert record is not None
    assert record.academy_manager_review is False


def test_no_role_student_cannot_submit(db, users):
    # Roleless students are mid-onboarding and can't edit their profile at
    # all (access.has_any_role) — there's no "admin only" review route left
    # to hit, since they can never create the item in the first place.
    student = _no_role_user(db, "norole@academy.test")
    with pytest.raises(PermissionError):
        _submit_skill(db, student, student)


# ---- who sees the queue / who can act --------------------------------------
def _queue_records(db, reviewer, *, item_type=None):
    return [
        e["record"] for e in services.review_queue(db, reviewer, item_type=item_type)
    ]


def test_mentor_sees_only_their_own_submissions(db, users, monkeypatch):
    mentee = _user(db, "mentee@academy.test", "foundation")
    skill, _ = _submit_skill(db, mentee, mentee)
    # No mentor on file for the mentee -> nothing in the mentor's queue.
    assert _queue_records(db, users["mentor"]) == []
    # A submission routed to the mentor appears in their queue.
    monkeypatch.setattr(
        services.mentorship, "lookup_mentor", lambda db, student: users["mentor"]
    )
    skill2 = services.create_skill(
        db,
        mentee.id,
        {"name": "SQL", "category": "programming_languages"},
        mentee,
    )
    record = services.submit_for_review(db, "skill", skill2, mentee)
    assert record.assigned_mentor_id == users["mentor"].id
    assert [r.id for r in _queue_records(db, users["mentor"])] == [record.id]


def test_academy_manager_sees_only_junior_tier_submissions(db, users):
    junior = _user(db, "junior@academy.test", "foundation")
    senior = _user(db, "senior@academy.test", "talent")
    _, junior_record = _submit_skill(db, junior, junior)
    _, senior_record = _submit_skill(db, senior, senior)

    seen = _queue_records(db, users["manager"])
    assert [r.id for r in seen] == [junior_record.id]
    assert services.can_act_on(junior_record, users["manager"])
    assert not services.can_act_on(senior_record, users["manager"])


def test_admin_sees_every_submission(db, users):
    junior = _user(db, "junior@academy.test", "foundation")
    senior = _user(db, "senior@academy.test", "talent")
    _, junior_record = _submit_skill(db, junior, junior)
    _, senior_record = _submit_skill(db, senior, senior)

    seen = _queue_records(db, users["admin"])
    assert {r.id for r in seen} == {junior_record.id, senior_record.id}
    assert services.can_act_on(junior_record, users["admin"])
    assert services.can_act_on(senior_record, users["admin"])


def test_mentor_can_act_on_own_submission_regardless_of_tier(db, users, monkeypatch):
    monkeypatch.setattr(
        services.mentorship, "lookup_mentor", lambda db, student: users["mentor"]
    )
    for tier in ("foundation", "talent"):
        student = _user(db, f"{tier}-mentee@academy.test", tier)
        skill, record = _submit_skill(db, student, student)
        assert services.can_act_on(record, users["mentor"])


def test_other_mentor_cannot_act(db, users, monkeypatch):
    monkeypatch.setattr(
        services.mentorship, "lookup_mentor", lambda db, student: users["mentor"]
    )
    other = _user(db, "other@academy.test", "mentor")
    student = _user(db, "mentee@academy.test", "foundation")
    skill, record = _submit_skill(db, student, student)
    assert not services.can_act_on(record, other)


def test_nobody_reviews_their_own_submission(db, users):
    student = _user(db, "foundation-sa@academy.test", "foundation")
    skill, record = _submit_skill(db, student, student)
    assert not services.can_act_on(record, student, skill)
    assert services.can_act_on(record, users["admin"], skill)
