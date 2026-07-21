import os

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

load_dotenv()

TEST_DATABASE_URL = None


def require_mysql_test_database_url():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.exit(
            "TEST_DATABASE_URL is required and must point to a dedicated MySQL test database.",
            returncode=2,
        )

    parsed_url = make_url(database_url)
    if not parsed_url.drivername.startswith(("mysql", "mariadb")):
        pytest.exit("TEST_DATABASE_URL must use a MySQL database URL.", returncode=2)

    database_name = parsed_url.database or ""
    if "test" not in database_name.lower():
        pytest.exit(
            "TEST_DATABASE_URL database name must include 'test' because tests reset the schema.",
            returncode=2,
        )

    return database_url


def verify_mysql_test_database_connection(database_url):
    engine = create_engine(database_url, pool_pre_ping=True)

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        pytest.exit(
            f"Unable to connect to TEST_DATABASE_URL MySQL database: {exc}",
            returncode=2,
        )
    finally:
        engine.dispose()


def configure_runtime_db_env_from_test_url(database_url):
    parsed_url = make_url(database_url)
    os.environ["DB_HOST"] = parsed_url.host or "localhost"
    os.environ["DB_NAME"] = parsed_url.database or ""
    os.environ["DB_USER"] = parsed_url.username or ""
    os.environ["DB_PASSWORD"] = parsed_url.password or ""


def pytest_configure(config):
    global TEST_DATABASE_URL

    TEST_DATABASE_URL = require_mysql_test_database_url()
    verify_mysql_test_database_connection(TEST_DATABASE_URL)
    configure_runtime_db_env_from_test_url(TEST_DATABASE_URL)


@pytest.fixture(scope="session")
def app():
    import app as app_module

    app_module.app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=TEST_DATABASE_URL,
        SECRET_KEY="test-secret",
        WTF_CSRF_ENABLED=False,
    )
    return app_module.app


@pytest.fixture
def db(app):
    from extensions import db as database
    from models import Role

    with app.app_context():
        database.drop_all()
        database.create_all()
        for role_name in Role.DEFAULT_NAMES:
            database.session.add(Role(name=role_name))
        database.session.commit()
        yield database
        database.session.remove()
        database.drop_all()


@pytest.fixture
def client(app, db):
    return app.test_client()


@pytest.fixture
def user_factory(db):
    from models import Role, User

    def create_user(
        email="user@example.com",
        password="password123",
        role_name=Role.NAME_ADMIN,
        is_active=True,
    ):
        role = Role.query.filter_by(name=role_name).one()
        user = User(
            email=User.normalize_email(email),
            role=role,
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
