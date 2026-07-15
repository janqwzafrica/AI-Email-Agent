from app import is_safe_redirect_url


def test_safe_redirect_allows_internal_absolute_path():
    assert is_safe_redirect_url("/dashboard") is True


def test_safe_redirect_rejects_missing_values():
    assert is_safe_redirect_url(None) is False
    assert is_safe_redirect_url("") is False


def test_safe_redirect_rejects_external_url():
    assert is_safe_redirect_url("https://example.com") is False


def test_safe_redirect_rejects_protocol_relative_url():
    assert is_safe_redirect_url("//example.com/path") is False


def test_safe_redirect_rejects_relative_path_without_leading_slash():
    assert is_safe_redirect_url("dashboard") is False
