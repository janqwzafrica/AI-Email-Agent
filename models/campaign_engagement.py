from extensions import db
from models.base import BaseEntity


class CampaignEngagement(BaseEntity):
    """Open/click tracking for SMTP-delivered campaigns. Brevo tracks this
    itself when it sends a campaign, but SMTP sends bypass that entirely, so
    this is our own record of the same signal for that delivery path."""

    __tablename__ = "campaign_engagements"

    EVENT_OPEN = "open"
    EVENT_CLICK = "click"
    EVENT_UNSUBSCRIBE = "unsubscribe"

    campaign_id = db.Column(db.String(36), db.ForeignKey("email_campaigns.id"), nullable=False, index=True)
    contact_email = db.Column(db.String(255), nullable=False, index=True)
    event_type = db.Column(db.String(20), nullable=False)
    url = db.Column(db.String(1000))

    campaign = db.relationship(
        "EmailCampaign",
        backref=db.backref("engagements", lazy=True, cascade="all, delete-orphan"),
    )
