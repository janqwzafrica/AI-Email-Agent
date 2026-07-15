from datetime import datetime, timezone
from secrets import randbelow

from flask_login import UserMixin
from sqlalchemy import CheckConstraint
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db, login_manager


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    ROLE_ADMIN = "Admin"
    ROLE_STAFF = "Staff"
    VALID_ROLES = (ROLE_ADMIN, ROLE_STAFF)

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    first_name = db.Column(db.String(80))
    last_name = db.Column(db.String(80))
    contact_number = db.Column(db.String(40))
    access_level = db.Column(db.String(20), nullable=False, default=ROLE_ADMIN)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint(
            "access_level IN ('Admin', 'Staff')",
            name="ck_users_access_level",
        ),
    )

    @staticmethod
    def normalize_email(email):
        return (email or "").strip().lower()

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class PasswordResetCode(db.Model):
    __tablename__ = "password_reset_codes"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    code_hash = db.Column(db.String(255), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    used_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    user = db.relationship(
        "User",
        backref=db.backref("password_reset_codes", lazy=True),
    )

    @staticmethod
    def generate_code():
        return f"{randbelow(1_000_000):06d}"

    def set_code(self, code):
        self.code_hash = generate_password_hash(code)

    def check_code(self, code):
        return check_password_hash(self.code_hash, (code or "").strip())

    @property
    def is_expired(self):
        return self.expires_at <= utc_now()

    @property
    def is_used(self):
        return self.used_at is not None

    def is_valid_for(self, code):
        return not self.is_used and not self.is_expired and self.check_code(code)

    def mark_used(self):
        self.used_at = utc_now()


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None
