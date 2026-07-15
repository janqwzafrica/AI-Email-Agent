from secrets import randbelow

from extensions import db
from models.base import BaseEntity, utc_now
from utils.security import check_secret_hash, hash_secret


class PasswordResetCode(BaseEntity):
    __tablename__ = "password_reset_codes"

    user_id = db.Column(
        db.String(36),
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
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
