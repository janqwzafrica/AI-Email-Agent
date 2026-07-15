from uuid import UUID

from models import Role, User


def test_signup_screen_renders(client):
    response = client.get("/signup")

    assert response.status_code == 200
    assert b"Create an Account" in response.data


def test_signup_redirects_authenticated_user(logged_in_client):
    response = logged_in_client.get("/signup")

    assert response.status_code == 302
    assert response.location == "/dashboard"


def test_signup_requires_email(client):
    response = client.post(
        "/signup",
        data={
            "email": "",
            "password": "password123",
            "password_confirm": "password123",
        },
    )

    assert response.status_code == 400
    assert b"Email is required." in response.data


def test_signup_rejects_short_password(client):
    response = client.post(
        "/signup",
        data={
            "email": "user@example.com",
            "password": "short",
            "password_confirm": "short",
        },
    )

    assert response.status_code == 400
    assert b"Password must be at least 8 characters." in response.data


def test_signup_rejects_passwords_over_bcrypt_limit(client):
    long_password = "a" * 73

    response = client.post(
        "/signup",
        data={
            "email": "user@example.com",
            "password": long_password,
            "password_confirm": long_password,
        },
    )

    assert response.status_code == 400
    assert b"Password must be 72 bytes or fewer." in response.data


def test_signup_rejects_mismatched_passwords(client):
    response = client.post(
        "/signup",
        data={
            "email": "user@example.com",
            "password": "password123",
            "password_confirm": "different123",
        },
    )

    assert response.status_code == 400
    assert b"Passwords do not match." in response.data


def test_signup_creates_user_and_redirects_to_login(client, db):
    response = client.post(
        "/signup",
        data={
            "email": "NewUser@Example.COM ",
            "password": "password123",
            "password_confirm": "password123",
        },
    )

    user = User.query.filter_by(email="newuser@example.com").one()
    assert response.status_code == 302
    assert response.location == "/login"
    assert UUID(user.id)
    assert user.check_password("password123") is True
    assert user.password_hash.startswith("$2b$")
    assert user.role.name == Role.NAME_ADMIN


def test_signup_rejects_duplicate_email(client, user_factory):
    user_factory(email="duplicate@example.com")

    response = client.post(
        "/signup",
        data={
            "email": "DUPLICATE@example.com",
            "password": "password123",
            "password_confirm": "password123",
        },
    )

    assert response.status_code == 409
    assert b"An account with this email already exists." in response.data
