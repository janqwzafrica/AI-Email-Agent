from extensions import db
from models.base import BaseEntity

AI_EMAIL_AGENT_CONTACT_LIST_ID = 'AEA-PF-CONTACT_LIST-6E37CD18'

class ContactList(BaseEntity):
    __tablename__ = "contact_lists"

    brevo_list_id = db.Column(db.Integer, nullable=False, unique=True, index=True)
    name = db.Column(db.String(255), nullable=False)
