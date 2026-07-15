import pytest

import app as app_module
from extensions import db as database
from models import User


@pytest.fixture(scope="session")
def app(tmp_path_factory):
    test_db = tmp_path_factory.mktemp("data") / "test.db"
    app_module.app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{test_db}",
        SECRET_KEY="test-secret",
        WTF_CSRF_ENABLED=False,
    )
    return app_module.app


@pytest.fixture
def db(app):
    with app.app_context():
        database.drop_all()
        database.create_all()
        yield database
        database.session.remove()
        database.drop_all()


@pytest.fixture
def client(app, db):
    return app.test_client()


@pytest.fixture
def user_factory(db):
    def create_user(
        email="user@example.com",
        password="password123",
        access_level=User.ROLE_ADMIN,
        is_active=True,
    ):
        user = User(
            email=User.normalize_email(email),
            access_level=access_level,
            is_active=is_active,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return user

    return create_user


@pytest.fixture
def logged_in_client(client, user_factory):
    user_factory(email="logged-in@example.com", password="password123")
    client.post(
        "/login",
        data={"email": "logged-in@example.com", "password": "password123"},
    )
    return client
