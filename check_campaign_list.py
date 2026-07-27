"""
Run once from your project root: python reset_all_stub_campaigns.py

Finds every EmailCampaign whose brevo_campaign_id still starts with the fake
"stub-" prefix left over from BREVO_STUB_MODE testing, and clears it so the
next save recreates a real Brevo campaign via create_email_campaign().
"""
from app import app, db
from models import EmailCampaign

with app.app_context():
    stub_campaigns = EmailCampaign.query.filter(
        EmailCampaign.brevo_campaign_id.like("stub-%")
    ).all()

    if not stub_campaigns:
        print("No campaigns with a stub brevo_campaign_id found. Nothing to do.")
        raise SystemExit

    print(f"Found {len(stub_campaigns)} campaign(s) with a stub brevo_campaign_id:\n")
    for c in stub_campaigns:
        print(f"  id={c.id}  brevo_campaign_id={c.brevo_campaign_id!r}  status={c.status}")

    confirm = input("\nClear brevo_campaign_id on all of these? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted — no changes made.")
        raise SystemExit

    for c in stub_campaigns:
        c.brevo_campaign_id = None

    db.session.commit()
    print(f"\nCleared brevo_campaign_id on {len(stub_campaigns)} campaign(s). "
          "Re-save each one in the wizard to have it create a real Brevo campaign.")