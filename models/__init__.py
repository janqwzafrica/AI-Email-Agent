from datetime import datetime, timezone
from secrets import randbelow
from uuid import uuid4

from flask_login import UserMixin

from extensions import db, login_manager
from utils.security import check_secret_hash, hash_secret


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def generate_uuid():
    return str(uuid4())


class BaseEntity(db.Model):
    __abstract__ = True

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


class Role(BaseEntity):
    __tablename__ = "roles"

    NAME_ADMIN = "Admin"
    NAME_STAFF = "Staff"
    DEFAULT_NAMES = (NAME_ADMIN, NAME_STAFF)

    name = db.Column(db.String(50), nullable=False, unique=True, index=True)


class User(UserMixin, BaseEntity):
    __tablename__ = "users"

    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    first_name = db.Column(db.String(80))
    last_name = db.Column(db.String(80))
    contact_number = db.Column(db.String(40))
    role_id = db.Column(db.String(36), db.ForeignKey("roles.id"), nullable=False, index=True)
    role = db.relationship("Role", backref=db.backref("users", lazy=True))
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    @staticmethod
    def normalize_email(email):
        return (email or "").strip().lower()

    def set_password(self, password):
        self.password_hash = hash_secret(password)

    def check_password(self, password):
        return check_secret_hash(self.password_hash, password)

    @property
    def access_level(self):
        return self.role.name if self.role else None


class PasswordResetCode(BaseEntity):
    __tablename__ = "password_reset_codes"

    user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False, index=True)
    code_hash = db.Column(db.String(255), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    used_at = db.Column(db.DateTime)

    user = db.relationship(
        "User",
        backref=db.backref("password_reset_codes", lazy=True),
    )

    @staticmethod
    def generate_code():
        return f"{randbelow(1_000_000):06d}"

    def set_code(self, code):
        self.code_hash = hash_secret(code)

    def check_code(self, code):
        return check_secret_hash(self.code_hash, (code or "").strip())

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
        return db.session.get(User, user_id)
    except (TypeError, ValueError):
        return None
