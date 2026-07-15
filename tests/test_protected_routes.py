def test_root_redirects_anonymous_user_to_login(client):
    response = client.get("/")

    assert response.status_code == 302
    assert response.location == "/login"


def test_root_redirects_authenticated_user_to_dashboard(logged_in_client):
    response = logged_in_client.get("/")

    assert response.status_code == 302
    assert response.location == "/dashboard"


def test_dashboard_redirects_anonymous_user_to_login(client):
    response = client.get("/dashboard")

    assert response.status_code == 302
    assert "/login" in response.location


def test_dashboard_renders_for_authenticated_user(logged_in_client):
    response = logged_in_client.get("/dashboard")

    assert response.status_code == 200
