import os
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()


def require_env(env_var):
    value = (os.getenv(env_var) or "").strip()
    if not value:
        raise RuntimeError(
            f"{env_var} is required. Set DB_HOST, DB_NAME, DB_USER, "
            "and DB_PASSWORD in .env."
        )
    return value


def mysql_database_url():
    """Build the app's MySQL URL from simple DB_* pieces.

    Keeping deploy-time config as DB_HOST/DB_NAME/DB_USER/DB_PASSWORD matches
    the team's other Flask services and avoids asking non-dev deployers to
    edit a long SQLAlchemy connection string by hand.
    """
    host = (os.getenv("DB_HOST") or "localhost").strip()
    name = quote_plus(require_env("DB_NAME"))
    user = quote_plus(require_env("DB_USER"))
    password = quote_plus(os.getenv("DB_PASSWORD") or "")

    return f"mysql+pymysql://{user}:{password}@{host}/{name}?charset=utf8mb4"


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
    SQLALCHEMY_DATABASE_URI = mysql_database_url()
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SMTP_HOST = os.getenv("SMTP_HOST")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME = os.getenv("SMTP_USERNAME")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
    SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes")
    SMTP_DEFAULT_SENDER = os.getenv("SMTP_DEFAULT_SENDER") or SMTP_USERNAME
    PASSWORD_RESET_CODE_MINUTES = int(os.getenv("PASSWORD_RESET_CODE_MINUTES", "10"))
    PASSWORD_SETUP_LINK_HOURS = int(os.getenv("PASSWORD_SETUP_LINK_HOURS", "48"))
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    BREVO_WEBHOOK_SECRET = os.getenv("BREVO_WEBHOOK_SECRET")
    PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
