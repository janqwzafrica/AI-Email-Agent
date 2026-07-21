from extensions import db
from models.base import BaseEntity


class EmailCampaign(BaseEntity):
    __tablename__ = "email_campaigns"

    MODE_AI = "ai"
    MODE_MANUAL = "manual"
    MODES = (MODE_AI, MODE_MANUAL)

    STATUS_DRAFT = "draft"
    STATUS_GENERATING = "generating"
    STATUS_READY = "ready"
    STATUS_SCHEDULED = "scheduled"
    STATUS_SENT = "sent"

    RECURRENCE_QUARTERLY = "quarterly"

    created_by_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=False, index=True)
    mode = db.Column(db.String(10), nullable=False)
    status = db.Column(db.String(20), nullable=False, default=STATUS_DRAFT)
    brevo_campaign_id = db.Column(db.String(64), unique=True, index=True)
    contact_list_id = db.Column(db.String(36), db.ForeignKey("contact_lists.id"), index=True)

    subject = db.Column(db.String(255))
    sender_name = db.Column(db.String(120))
    sender_email = db.Column(db.String(255))

    content_file_name = db.Column(db.String(255))
    extracted_text = db.Column(db.Text)
    cta_links = db.Column(db.Text)
    logo_url = db.Column(db.String(255))
    email_content = db.Column(db.Text)
    is_generating = db.Column(db.Boolean, nullable=False, default=False)

    scheduled_at = db.Column(db.DateTime)
    recurrence_rule = db.Column(db.String(20))
    next_run_at = db.Column(db.DateTime)

    created_by = db.relationship("User", foreign_keys=[created_by_id])
    contact_list = db.relationship("ContactList", foreign_keys=[contact_list_id])
