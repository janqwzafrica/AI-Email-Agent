def test_login_screen_renders(client):
    response = client.get("/login")

    assert response.status_code == 200
    assert b"Log In" in response.data


def test_login_redirects_authenticated_user(logged_in_client):
    response = logged_in_client.get("/login")

    assert response.status_code == 302
    assert response.location == "/dashboard"


def test_login_rejects_unknown_user(client):
    response = client.post(
        "/login",
        data={"email": "missing@example.com", "password": "password123"},
    )

    assert response.status_code == 401
    assert b"Invalid email or password." in response.data


def test_login_rejects_wrong_password(client, user_factory):
    user_factory(email="user@example.com", password="password123")

    response = client.post(
        "/login",
        data={"email": "user@example.com", "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert b"Invalid email or password." in response.data


def test_login_rejects_inactive_user(client, user_factory):
    user_factory(email="inactive@example.com", password="password123", is_active=False)

    response = client.post(
        "/login",
        data={"email": "inactive@example.com", "password": "password123"},
    )

    assert response.status_code == 403
    assert b"This account is inactive." in response.data


def test_login_redirects_to_dashboard_on_success(client, user_factory):
    user_factory(email="user@example.com", password="password123")

    response = client.post(
        "/login",
        data={"email": "USER@example.com", "password": "password123"},
    )

    assert response.status_code == 302
    assert response.location == "/dashboard"


def test_login_honors_safe_next_url(client, user_factory):
    user_factory(email="user@example.com", password="password123")

    response = client.post(
        "/login?next=/reports",
        data={"email": "user@example.com", "password": "password123"},
    )

    assert response.status_code == 302
    assert response.location == "/reports"


def test_login_ignores_unsafe_next_url(client, user_factory):
    user_factory(email="user@example.com", password="password123")

    response = client.post(
        "/login?next=https://example.com",
        data={"email": "user@example.com", "password": "password123"},
    )

    assert response.status_code == 302
    assert response.location == "/dashboard"


def test_logout_requires_authentication(client):
    response = client.get("/logout")

    assert response.status_code == 302
    assert "/login" in response.location


def test_logout_clears_session(logged_in_client):
    response = logged_in_client.get("/logout")

    assert response.status_code == 302
    assert response.location == "/login"

    dashboard_response = logged_in_client.get("/dashboard")
    assert dashboard_response.status_code == 302
    assert "/login" in dashboard_response.location
