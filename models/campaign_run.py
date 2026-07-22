from extensions import db
from models.base import BaseEntity


class CampaignRun(BaseEntity):
    __tablename__ = "campaign_runs"

    STATUS_SCHEDULED = "scheduled"
    STATUS_RUNNING = "running"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"

    campaign_id = db.Column(db.String(36), db.ForeignKey("email_campaigns.id"), nullable=False, index=True)
    brevo_campaign_id = db.Column(db.Integer, nullable=False)
    run_at = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), nullable=False, default=STATUS_SCHEDULED)

    campaign = db.relationship(
        "EmailCampaign",
        backref=db.backref("runs", lazy=True, cascade="all, delete-orphan"),
    )
