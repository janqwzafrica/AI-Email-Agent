import os

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{os.path.join(BASE_DIR, 'app.db')}",
    )
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
