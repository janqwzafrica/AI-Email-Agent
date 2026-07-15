from models import PasswordResetCode, User


def test_forgot_password_screen_renders(client):
    response = client.get("/forgot-password")

    assert response.status_code == 200
    assert b"Forgot" in response.data


def test_forgot_password_redirects_authenticated_user(logged_in_client):
    response = logged_in_client.get("/forgot-password")

    assert response.status_code == 302
    assert response.location == "/dashboard"


def test_forgot_password_reset_requires_email_and_code(client):
    response = client.post(
        "/forgot-password",
        data={
            "email": "",
            "auth_code": "",
            "password": "password123",
            "password_confirm": "password123",
        },
    )

    assert response.status_code == 400
    assert b"Email and reset code are required." in response.data


def test_forgot_password_reset_rejects_short_password(client):
    response = client.post(
        "/forgot-password",
        data={
            "email": "user@example.com",
            "auth_code": "123456",
            "password": "short",
            "password_confirm": "short",
        },
    )

    assert response.status_code == 400
    assert b"Password must be at least 8 characters." in response.data


def test_forgot_password_reset_rejects_passwords_over_bcrypt_limit(client):
    long_password = "a" * 73

    response = client.post(
        "/forgot-password",
        data={
            "email": "user@example.com",
            "auth_code": "123456",
            "password": long_password,
            "password_confirm": long_password,
        },
    )

    assert response.status_code == 400
    assert b"Password must be 72 bytes or fewer." in response.data


def test_forgot_password_reset_rejects_mismatched_passwords(client):
    response = client.post(
        "/forgot-password",
        data={
            "email": "user@example.com",
            "auth_code": "123456",
            "password": "password123",
            "password_confirm": "different123",
        },
    )

    assert response.status_code == 400
    assert b"Passwords do not match." in response.data


def test_forgot_password_reset_rejects_invalid_code(client, user_factory):
    user_factory(email="user@example.com", password="old-password")

    response = client.post(
        "/forgot-password",
        data={
            "email": "user@example.com",
            "auth_code": "123456",
            "password": "new-password123",
            "password_confirm": "new-password123",
        },
    )

    assert response.status_code == 400
    assert b"Invalid or expired reset code." in response.data


def test_send_code_requires_email(client):
    response = client.post("/forgot-password/send-code", data={"email": ""})

    assert response.status_code == 400
    assert response.json == {"success": False, "message": "Email is required."}


def test_send_code_for_unknown_account_returns_generic_success(client):
    response = client.post(
        "/forgot-password/send-code",
        data={"email": "missing@example.com"},
    )

    assert response.status_code == 200
    assert response.json["success"] is True
    assert "If an account exists" in response.json["message"]


def test_send_code_for_known_account_creates_reset_code(client, user_factory, mocker):
    user = user_factory(email="user@example.com")
    send_email = mocker.patch("app.send_password_reset_email")

    response = client.post(
        "/forgot-password/send-code",
        data={"email": "USER@example.com"},
    )

    reset_code = PasswordResetCode.query.filter_by(user_id=user.id, used_at=None).one()
    assert response.status_code == 200
    assert response.json["success"] is True
    assert reset_code.user_id == user.id
    assert reset_code.code_hash.startswith("$2b$")
    send_email.assert_called_once()


def test_successful_password_reset_changes_password(client, db, user_factory, mocker):
    user = user_factory(email="user@example.com", password="old-password123")
    captured_code = {}

    def fake_send_email(_email, code):
        captured_code["value"] = code

    mocker.patch("app.send_password_reset_email", side_effect=fake_send_email)
    client.post("/forgot-password/send-code", data={"email": "user@example.com"})

    response = client.post(
        "/forgot-password",
        data={
            "email": "user@example.com",
            "auth_code": captured_code["value"],
            "password": "new-password123",
            "password_confirm": "new-password123",
        },
    )

    db.session.refresh(user)
    reset_code = PasswordResetCode.query.filter_by(user_id=user.id).one()
    assert response.status_code == 302
    assert response.location == "/login"
    assert user.check_password("new-password123") is True
    assert reset_code.used_at is not None
