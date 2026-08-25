import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import engine, Base
from app.identity.models import Role, User


@pytest.fixture(scope="session")
def db_engine():
    assert engine.url.database == "test_db" or engine.url.database.endswith(
        "_test"
    ), "tests require a test database"
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    Base.metadata.create_all(engine)
    yield engine
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))


@pytest.fixture
def db(db_engine):
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def make_user(db):
    roles: dict[str, Role] = {}

    def _make(email, role_name, is_superuser=False):
        if role_name not in roles:
            r = Role(name=role_name, display_name=role_name)
            db.add(r)
            db.flush()
            roles[role_name] = r
        u = User(email=email, full_name=email.split("@")[0], password_hash="x")
        u.is_superuser = is_superuser
        u.roles = [roles[role_name]]
        db.add(u)
        db.flush()
        return u

    return _make


@pytest.fixture
def users(make_user):
    return {
        "owner": make_user("owner@academy.test", "student"),
        "stranger": make_user("stranger@academy.test", "student"),
        "mentor": make_user("mentor@academy.test", "mentor"),
        "hr": make_user("hr@academy.test", "hr"),
        "manager": make_user("manager@academy.test", "academy-manager"),
        "admin": make_user("admin@academy.test", "superadmin", is_superuser=True),
    }
