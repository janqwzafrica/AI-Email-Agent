from extensions import db
from models.base import BaseEntity


class ContactList(BaseEntity):
    __tablename__ = "contact_lists"

    brevo_list_id = db.Column(db.Integer, nullable=False, unique=True, index=True)
    name = db.Column(db.String(255), nullable=False)