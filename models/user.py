from flask_login import UserMixin

from extensions import db, login_manager
from models.base import BaseEntity
from utils.security import check_secret_hash, hash_secret


class User(UserMixin, BaseEntity):
    __tablename__ = "users"

    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    first_name = db.Column(db.String(80))
    last_name = db.Column(db.String(80))
    contact_number = db.Column(db.String(40))
    role_id = db.Column(
        db.String(36),
        db.ForeignKey("roles.id"),
        nullable=False,
        index=True,
    )
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


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, user_id)
    except (TypeError, ValueError):
        return None
