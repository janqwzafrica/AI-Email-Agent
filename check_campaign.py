from app import app, db
from models import EmailCampaign

with app.app_context():
    campaigns = EmailCampaign.query.order_by(EmailCampaign.id.desc()).limit(3).all()
    for c in campaigns:
        print("id:", c.id, "| brevo_campaign_id:", repr(c.brevo_campaign_id), "| status:", c.status, "| mode:", getattr(c, "mode", None))