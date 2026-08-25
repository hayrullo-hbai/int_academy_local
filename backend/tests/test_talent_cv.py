import pytest

from app.talent import access


@pytest.fixture
def learner(make_user):
    return make_user("learner@academy.test", "talent")


@pytest.fixture
def mentor(make_user):
    return make_user("mentor@academy.test", "mentor")


@pytest.fixture
def manager(make_user):
    return make_user("manager@academy.test", "academy-manager")


@pytest.fixture
def hr(make_user):
    return make_user("hr@academy.test", "hr")


@pytest.fixture
def admin(make_user):
    return make_user("admin@academy.test", "superadmin", is_superuser=True)


@pytest.fixture
def stranger(make_user):
    return make_user("stranger@academy.test", "student")


@pytest.fixture
def examiner(make_user):
    return make_user("examiner@academy.test", "examiner")


def test_owner_learner_can_generate_cv(learner):
    assert access.can_generate_cv(learner, learner)


def test_non_learner_owner_cannot_generate_cv(stranger):
    assert not access.can_generate_cv(stranger, stranger)


def test_mentor_can_generate_cv_for_learner(mentor, learner):
    assert access.can_generate_cv(mentor, learner)


def test_manager_can_generate_cv_for_learner(manager, learner):
    assert access.can_generate_cv(manager, learner)


def test_hr_can_generate_cv_for_learner(hr, learner):
    assert access.can_generate_cv(hr, learner)


def test_admin_can_generate_cv_for_learner(admin, learner):
    assert access.can_generate_cv(admin, learner)


def test_stranger_cannot_generate_cv_for_learner(stranger, learner):
    assert not access.can_generate_cv(stranger, learner)


def test_staff_cannot_generate_cv_for_non_learner(mentor, stranger):
    assert not access.can_generate_cv(mentor, stranger)


def test_examiner_cannot_generate_cv_for_learner(examiner, learner):
    assert not access.can_generate_cv(examiner, learner)


def test_mentor_can_view_learner_cv(mentor, learner):
    assert access.can_view_cv(mentor, learner)


def test_owner_can_view_own_cv(learner):
    assert access.can_view_cv(learner, learner)


def test_stranger_cannot_view_learner_cv(stranger, learner):
    assert not access.can_view_cv(stranger, learner)
