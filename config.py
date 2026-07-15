import os
from urllib.parse import urlsplit

from dotenv import load_dotenv

load_dotenv()


def mysql_database_url(env_var):
    database_url = (os.getenv(env_var) or "").strip()

    if not database_url:
        raise RuntimeError(
            f"{env_var} is required. Set it to a MySQL SQLAlchemy URL, "
            "for example mysql+pymysql://user:password@host:3306/database."
        )

    scheme = urlsplit(database_url).scheme
    if not scheme.startswith(("mysql", "mariadb")):
        raise RuntimeError(
            f"{env_var} must point to MySQL. Received URL scheme '{scheme or '<missing>'}'."
        )

    return database_url


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
    SQLALCHEMY_DATABASE_URI = mysql_database_url("DATABASE_URL")
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
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
