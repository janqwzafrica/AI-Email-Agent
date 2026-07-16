from extensions import db
from models.base import BaseEntity


class TestEmail(BaseEntity):
    __tablename__ = "test_emails"

    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    name = db.Column(db.String(160))
    added_by_id = db.Column(db.String(36), db.ForeignKey("users.id"), index=True)

    added_by = db.relationship("User", foreign_keys=[added_by_id])

    @staticmethod
    def normalize_email(email):
        return (email or "").strip().lower()
