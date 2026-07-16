from extensions import db
from models.base import BaseEntity


class Contact(BaseEntity):
    __tablename__ = "contacts"

    CLASSIFICATION_INTERESTED = "Interested"
    CLASSIFICATION_NOT_INTERESTED = "Not Interested"
    CLASSIFICATION_PENDING = "Pending"
    CLASSIFICATION_NO_RESPONSE = "No Response"
    CLASSIFICATION_UNSUBSCRIBED = "Unsubscribed"
    CLASSIFICATIONS = (
        CLASSIFICATION_INTERESTED,
        CLASSIFICATION_NOT_INTERESTED,
        CLASSIFICATION_PENDING,
        CLASSIFICATION_NO_RESPONSE,
        CLASSIFICATION_UNSUBSCRIBED,
    )

    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    first_name = db.Column(db.String(80))
    last_name = db.Column(db.String(80))
    classification = db.Column(db.String(40))
    is_blacklisted = db.Column(db.Boolean, nullable=False, default=False)
    last_webhook_event = db.Column(db.String(40))
    classified_by_id = db.Column(db.String(36), db.ForeignKey("users.id"), index=True)
    classified_at = db.Column(db.DateTime)

    classified_by = db.relationship("User", foreign_keys=[classified_by_id])

    @staticmethod
    def normalize_email(email):
        return (email or "").strip().lower()
