from datetime import datetime, timedelta, timezone
from functools import wraps
from html import unescape as html_unescape
from urllib.parse import quote, urlsplit

import base64
import calendar
import os
import re
import secrets
import threading
from flask import (
    Flask,
    abort,
    flash,
    current_app,
    redirect,
    render_template,
    request,
    url_for,
    jsonify,
    session,
)
from markupsafe import escape
from werkzeug.utils import secure_filename
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import IntegrityError

from config import Config
from extensions import db, login_manager, migrate
from logging_config import setup_logging
from models import (
    CampaignEngagement,
    CampaignRun,
    Contact,
    ContactList,
    EmailCampaign,
    PasswordResetCode,
    Role,
    TestEmail,
    User,
    utc_now,
)
from services.email_service import (
    send_campaign_email,
    send_password_reset_email,
    send_password_setup_email,
)
from utils.security import BCRYPT_MAX_BYTES
from tools.brevo import BrevoAPIError, BrevoClient
from services.document_extractor import extract_text, ExtractionError
from services.ai_email_content import generate_email_content, AIGenerationError
from http.client import error

setup_logging()

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)
migrate.init_app(app, db)
login_manager.init_app(app)


def is_safe_redirect_url(target):
    if not target:
        return False

    parsed_target = urlsplit(target)
    return (
        parsed_target.scheme == ""
        and parsed_target.netloc == ""
        and target.startswith("/")
    )


def is_within_bcrypt_limit(value):
    return len((value or "").encode("utf-8")) <= BCRYPT_MAX_BYTES


ALLOWED_CONTENT_EXTENSIONS = {"doc", "docx", "pdf"}
ALLOWED_LOGO_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # Max size: 5MB
LOGO_UPLOAD_DIR = os.path.join(app.static_folder, "uploads", "logos")
os.makedirs(LOGO_UPLOAD_DIR, exist_ok=True)
BREVO_STUB_MODE = os.environ.get("BREVO_STUB_MODE", "false").lower() == "true"
print(f"BREVO_STUB_MODE = {BREVO_STUB_MODE}")

def _allowed_file(filename, allowed_extensions):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed_extensions


def _default_subject_from_filename(filename):
    """A readable default subject derived from the uploaded document's name,
    for manual mode where there's no AI call to generate a real subject."""
    if not filename:
        return ""
    name = filename.rsplit(".", 1)[0]
    name = re.sub(r"[_-]+", " ", name).strip()
    return name.title() if name else ""


_ACTIVE_CAMPAIGN_STATUSES = (
    EmailCampaign.STATUS_DRAFT,
    EmailCampaign.STATUS_GENERATING,
    EmailCampaign.STATUS_READY,
)


def _get_or_create_campaign(mode):
    campaign_id = session.get("campaign_id")
    campaign = db.session.get(EmailCampaign, campaign_id) if campaign_id else None
    if campaign is None or campaign.status not in _ACTIVE_CAMPAIGN_STATUSES:
        campaign = EmailCampaign(
            created_by_id=current_user.id, mode=mode, status=EmailCampaign.STATUS_DRAFT
        )
        db.session.add(campaign)
        db.session.flush()
        session["campaign_id"] = campaign.id
    campaign.mode = mode
    return campaign


def get_current_campaign():
    campaign_id = session.get("campaign_id")
    if not campaign_id:
        return None
    return db.session.get(EmailCampaign, campaign_id)


FEEDBACK_LINK_CLASSIFICATIONS = {
    "/feedback/interested": Contact.CLASSIFICATION_INTERESTED,
    "/feedback/not-interested": Contact.CLASSIFICATION_NOT_INTERESTED,
}


def _feedback_footer_html():
    base = app.config["PUBLIC_BASE_URL"]
    # {{ contact.EMAIL }} is filled in per-recipient (personalization for
    # SMTP sends; Brevo's own merge-tag engine for Brevo sends) so these
    # links identify who clicked directly — SMTP-delivered mail has no
    # click-tracking layer, so the /webhooks/brevo click-forwarding path
    # never fires for it and can't be relied on alone.
    return (
        '<div style="margin-top:24px;padding-top:16px;border-top:1px solid #e5e7eb;">'
        "<p>Still interested in hearing from us?</p>"
        f'<a href="{base}/feedback/interested?email={{{{ contact.EMAIL }}}}">Yes, I\'m interested</a>'
        "&nbsp;|&nbsp;"
        f'<a href="{base}/feedback/not-interested?email={{{{ contact.EMAIL }}}}">Not right now</a>'
        "</div>"
    )


def _normalize_cta_url(raw):
    """A single line from the CTA Links textarea -> an http(s) URL, or None if
    it isn't a usable link (blank, contains whitespace, or an unsafe/unsupported
    scheme like javascript: or mailto:)."""
    raw = (raw or "").strip()
    if not raw or any(c.isspace() for c in raw):
        return None
    parsed = urlsplit(raw)
    if not parsed.scheme:
        parsed = urlsplit(f"https://{raw}")
    if parsed.scheme not in ("http", "https"):
        return None
    if not parsed.netloc or "." not in parsed.netloc:
        return None
    return parsed.geturl()


def _parse_cta_links(raw_text):
    """The CTA Links textarea (one link per line, free text) -> a deduped list
    of normalized URLs."""
    seen = set()
    urls = []
    for line in (raw_text or "").splitlines():
        url = _normalize_cta_url(line)
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _cta_buttons_html(buttons):
    """Render CTA buttons as email-safe HTML (inline styles — email clients
    strip <style> blocks and most CSS, so this can't rely on page CSS)."""
    return "".join(
        '<div style="margin:20px 0;">'
        f'<a href="{escape(button["url"])}" '
        'style="display:inline-block;background-color:#0b5fff;color:#ffffff;'
        "padding:12px 28px;border-radius:6px;text-decoration:none;font-weight:600;"
        f'font-family:Arial,Helvetica,sans-serif;font-size:15px;">{escape(button["label"])}</a>'
        "</div>"
        for button in buttons
    )


def _add_inline_spacing(html):
    """Inline margin/line-height on bare <p>/<ul>/<li> tags from the AI body.

    Email clients (Outlook's Word engine especially) don't reliably apply
    default block-level margins, so paragraphs render with no spacing unless
    it's inline on the tag itself.
    """
    html = re.sub(r"<p>", '<p style="margin:0 0 16px 0;line-height:1.6;">', html)
    html = re.sub(
        r"<ul>",
        '<ul style="margin:0 0 16px 0;padding-left:20px;line-height:1.6;">',
        html,
    )
    html = re.sub(r"<li>", '<li style="margin:0 0 8px 0;">', html)
    return html


def _logo_header_html(campaign):
    """Absolute-URL <img> for the campaign logo, meant only for the HTML
    actually sent to Brevo. Recipients' inboxes fetch images over the public
    internet, so a relative /static/... path (fine for the in-app preview)
    won't resolve for them."""
    if not campaign.logo_url:
        return ""
    base = app.config.get("PUBLIC_BASE_URL")
    logo_src = campaign.logo_url
    if base and logo_src.startswith("/"):
        logo_src = f"{base}{logo_src}"
    return (
        '<div style="margin-bottom:24px;">'
        f'<img src="{escape(logo_src)}" alt="" style="max-width:200px;height:auto;display:block;">'
        "</div>"
    )


def _compose_send_html(campaign):
    """The HTML actually handed to Brevo: logo header + the editable body."""
    return _logo_header_html(campaign) + (campaign.email_content or "")


def _personalize_campaign_html(html, attributes, email=None):
    """Fill in the {{ contact.FIRSTNAME }} / {{ contact.LASTNAME }} /
    {{ contact.EMAIL }} merge tags Brevo would normally resolve
    server-side — needed now that SMTP sends bypass Brevo's campaign
    engine entirely."""
    firstname = (attributes or {}).get("FIRSTNAME") or ""
    lastname = (attributes or {}).get("LASTNAME") or ""
    email_value = quote(email or "")
    for tag, value in (
        ("{{ contact.FIRSTNAME }}", firstname),
        ("{{contact.FIRSTNAME}}", firstname),
        ("{{ contact.LASTNAME }}", lastname),
        ("{{contact.LASTNAME}}", lastname),
        ("{{ contact.EMAIL }}", email_value),
        ("{{contact.EMAIL}}", email_value),
    ):
        html = html.replace(tag, value)
    return html


# Matches the exact <a> markup _cta_buttons_html() generates, so only those
# CTA buttons get click-tracking — feedback/unsubscribe links have their own
# dedicated identification and shouldn't be double-wrapped.
_CTA_LINK_RE = re.compile(r'<a href="([^"]*)" style="display:inline-block;background-color:#0b5fff')


def _wrap_cta_links_for_tracking(html, campaign_id):
    """Route each CTA button through /track/click first (SMTP sends have no
    click-tracking layer of their own, unlike Brevo). The {{ contact.EMAIL }}
    placeholder here is resolved later by _personalize_campaign_html."""
    base = app.config["PUBLIC_BASE_URL"]

    def _replace(match):
        original_url = html_unescape(match.group(1))
        tracking_url = (
            f"{base}/track/click?campaign_id={quote(campaign_id)}"
            f"&email={{{{ contact.EMAIL }}}}&to={quote(original_url, safe='')}"
        )
        return f'<a href="{escape(tracking_url)}" style="display:inline-block;background-color:#0b5fff'

    return _CTA_LINK_RE.sub(_replace, html)


def _open_tracking_pixel_html(campaign_id, email):
    base = app.config["PUBLIC_BASE_URL"]
    src = f"{base}/track/open?campaign_id={quote(campaign_id)}&email={quote(email)}"
    return f'<img src="{escape(src)}" width="1" height="1" alt="" style="display:none;border:0;">'


def _unsubscribe_footer_html(campaign_id, email):
    base = app.config["PUBLIC_BASE_URL"]
    href = f"{base}/unsubscribe?campaign_id={quote(campaign_id)}&email={quote(email)}"
    return (
        '<div style="margin-top:16px;padding-top:12px;font-size:12px;color:#9ca3af;">'
        "If you'd prefer not to receive further emails from us, you can "
        f'<a href="{escape(href)}" style="color:#9ca3af;">unsubscribe here</a>.'
        "</div>"
    )


def _smtp_campaign_recipients(campaign):
    """Recipient contacts for an SMTP-delivered send. Brevo still owns
    contact list storage either way (see CAMPAIGN_DELIVERY_PROVIDER) — only
    the outbound send step moves to SMTP — so membership is still read from
    the campaign's Brevo list, filtered against both Brevo's own blacklist
    flag and our locally tracked one."""
    if not campaign.contact_list:
        return []
    data = get_brevo_client().get_contacts_from_list(
        campaign.contact_list.brevo_list_id, limit=500
    )
    recipients = []
    for contact in data.get("contacts", []):
        email = contact.get("email")
        if not email or contact.get("emailBlacklisted"):
            continue
        local_contact = Contact.query.filter_by(
            email=Contact.normalize_email(email)
        ).one_or_none()
        if local_contact and local_contact.is_blacklisted:
            continue
        recipients.append({"email": email, "attributes": contact.get("attributes") or {}})
    return recipients


def send_campaign_via_smtp(campaign):
    """Send campaign to its full recipient list over the manual SMTP
    account. Returns (sent_count, failed_count); failures are logged
    per-recipient rather than aborting the whole run."""
    trackable_html = _wrap_cta_links_for_tracking(_compose_send_html(campaign), campaign.id)
    recipients = _smtp_campaign_recipients(campaign)
    sent = failed = 0
    for contact in recipients:
        try:
            html = _personalize_campaign_html(
                trackable_html, contact["attributes"], contact["email"]
            )
            html += _unsubscribe_footer_html(campaign.id, contact["email"])
            html += _open_tracking_pixel_html(campaign.id, contact["email"])
            send_campaign_email(
                contact["email"],
                campaign.subject or "Untitled campaign",
                html,
                sender_name=campaign.sender_name,
            )
            sent += 1
        except Exception:
            failed += 1
            app.logger.exception(
                "Failed to send campaign %s to %s over SMTP",
                campaign.id,
                contact["email"],
            )
    return sent, failed


def _dispatch_scheduled_smtp_campaign(app_ref, campaign_id):
    with app_ref.app_context():
        campaign = db.session.get(EmailCampaign, campaign_id)
        if not campaign or campaign.status != EmailCampaign.STATUS_SCHEDULED:
            return
        sent, failed = send_campaign_via_smtp(campaign)
        campaign.status = EmailCampaign.STATUS_SENT
        db.session.add(
            CampaignRun(
                campaign_id=campaign.id,
                brevo_campaign_id=campaign.brevo_campaign_id,
                run_at=utc_now(),
                status=CampaignRun.STATUS_SENT if failed == 0 else CampaignRun.STATUS_FAILED,
            )
        )
        db.session.commit()
        app.logger.info(
            "Scheduled SMTP campaign %s dispatched: %s sent, %s failed",
            campaign.id,
            sent,
            failed,
        )


# Windows' threading.Timer (WaitForSingleObjectEx under the hood) overflows
# past ~49.7 days — a DWORD milliseconds ceiling — so a single Timer can't
# wait out a whole quarter (up to ~92 days). Chain shorter waits instead.
_MAX_TIMER_DELAY_SECONDS = 20 * 24 * 60 * 60  # 20 days


def _start_delayed(run_at_utc, on_fire, on_new_timer=None):
    """Fire on_fire() once run_at_utc (tz-aware) is reached, chaining
    capped-length timers if the wait is longer than _MAX_TIMER_DELAY_SECONDS.

    on_new_timer, if given, is called with each new Timer segment as it's
    created — callers that need to support cancellation (e.g. a "Stop"
    button) must use this to keep their stored reference pointing at
    whichever segment is currently pending, since a Timer object from an
    already-fired earlier segment can't cancel a later one.
    """
    remaining = (run_at_utc - datetime.now(timezone.utc)).total_seconds()
    if remaining <= _MAX_TIMER_DELAY_SECONDS:
        timer = threading.Timer(max(remaining, 0), on_fire)
    else:
        timer = threading.Timer(
            _MAX_TIMER_DELAY_SECONDS,
            lambda: _start_delayed(run_at_utc, on_fire, on_new_timer),
        )
    timer.daemon = True
    if on_new_timer:
        on_new_timer(timer)
    timer.start()
    return timer


def _schedule_smtp_campaign(campaign, aware_dt):
    """Delay-dispatch a campaign at aware_dt using an in-process timer.

    Note: this only fires while this server process keeps running — a
    restart between now and the scheduled time loses the pending send
    (there's no persisted job queue backing SMTP delivery yet).
    """
    _start_delayed(
        aware_dt.astimezone(timezone.utc),
        lambda: _dispatch_scheduled_smtp_campaign(app, campaign.id),
    )


def _add_months(dt, months):
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _quarter_end(dt):
    """Last day of dt's calendar quarter, same time-of-day as dt."""
    quarter_end_month = ((dt.month - 1) // 3 + 1) * 3
    last_day = calendar.monthrange(dt.year, quarter_end_month)[1]
    return dt.replace(month=quarter_end_month, day=last_day)


def _first_quarterly_run_at(activated_at):
    """First automatic run: the end of the calendar quarter Activate was
    clicked in (any day in Jan/Feb/Mar -> March 31), not a fixed 90-day
    count from the activation date."""
    return _quarter_end(activated_at)


def _next_quarterly_run_at(previous_run_at):
    """previous_run_at is always a quarter-end; stepping one month past it
    always lands in the next quarter, whose end is the next run."""
    return _quarter_end(_add_months(previous_run_at, 1))


_quarterly_timers = {}
_quarterly_timers_lock = threading.Lock()


def _schedule_quarterly_timer(campaign_id, run_at):
    """Arm (or re-arm) the in-process timer for a campaign's next quarterly
    run. run_at is naive UTC, matching EmailCampaign.next_run_at.

    Quarterly gaps (up to ~92 days) can exceed a single Timer's max delay,
    so _start_delayed may chain several segments before the real dispatch —
    on_new_timer keeps the registry pointed at whichever segment is
    currently pending, so _cancel_quarterly_timer can always abort it.
    """
    with _quarterly_timers_lock:
        existing = _quarterly_timers.pop(campaign_id, None)
    if existing:
        existing.cancel()

    def _register(timer):
        with _quarterly_timers_lock:
            _quarterly_timers[campaign_id] = timer

    _start_delayed(
        run_at.replace(tzinfo=timezone.utc),
        lambda: _dispatch_quarterly_campaign(app, campaign_id),
        on_new_timer=_register,
    )


def _cancel_quarterly_timer(campaign_id):
    with _quarterly_timers_lock:
        existing = _quarterly_timers.pop(campaign_id, None)
    if existing:
        existing.cancel()


def _dispatch_quarterly_campaign(app_ref, campaign_id):
    with app_ref.app_context():
        campaign = db.session.get(EmailCampaign, campaign_id)
        if not campaign or campaign.recurrence_rule != EmailCampaign.RECURRENCE_QUARTERLY:
            # Deactivated or deleted since this timer was armed.
            return

        if app.config["CAMPAIGN_DELIVERY_PROVIDER"] == "smtp":
            sent, failed = send_campaign_via_smtp(campaign)
            run_status = (
                CampaignRun.STATUS_SENT if failed == 0 else CampaignRun.STATUS_FAILED
            )
        else:
            # Note: Brevo generally refuses to re-send a campaign object
            # that's already been sent once — same restriction we hit
            # deleting sent campaigns. This mirrors the manual "Run Now"
            # Brevo call for consistency; it's a known limitation of
            # reusing one brevo_campaign_id across quarterly runs.
            try:
                brevo = get_brevo_client()
                brevo.update_email_campaign(
                    campaign.brevo_campaign_id, html_content=_compose_send_html(campaign)
                )
                brevo.send_campaign_now(campaign.brevo_campaign_id)
                run_status = CampaignRun.STATUS_SENT
            except BrevoAPIError:
                app.logger.exception(
                    "Quarterly Brevo send failed for campaign %s", campaign.id
                )
                run_status = CampaignRun.STATUS_FAILED

        db.session.add(
            CampaignRun(
                campaign_id=campaign.id,
                brevo_campaign_id=campaign.brevo_campaign_id,
                run_at=utc_now(),
                status=run_status,
            )
        )
        campaign.next_run_at = _next_quarterly_run_at(campaign.next_run_at)
        db.session.commit()

        app.logger.info(
            "Quarterly campaign %s dispatched (%s); next run %s",
            campaign.id,
            run_status,
            campaign.next_run_at,
        )
        _schedule_quarterly_timer(campaign.id, campaign.next_run_at)


def _rearm_quarterly_campaigns():
    """Re-schedule timers for all active quarterly campaigns on startup —
    the in-process timers above don't survive a restart on their own."""
    with app.app_context():
        campaigns = EmailCampaign.query.filter(
            EmailCampaign.recurrence_rule == EmailCampaign.RECURRENCE_QUARTERLY
        ).all()
        for campaign in campaigns:
            run_at = campaign.next_run_at or _first_quarterly_run_at(utc_now())
            _schedule_quarterly_timer(campaign.id, run_at)
            app.logger.info(
                "Re-armed quarterly campaign %s for %s", campaign.id, run_at
            )


def _run_generation(app_ref, campaign_id):
    with app_ref.app_context():
        campaign = db.session.get(EmailCampaign, campaign_id)
        if not campaign:
            return
        try:
            cta_links = _parse_cta_links(campaign.cta_links)
            result = generate_email_content(
                extracted_text=campaign.extracted_text,
                cta_links=cta_links,
                sender_name=campaign.sender_name or "Larhdel Law",
            )
            campaign.email_content = (
                _add_inline_spacing(result["body_html"])
                + _cta_buttons_html(result["cta_buttons"])
                + _feedback_footer_html()
            )
            if result["subject"] and not campaign.subject:
                campaign.subject = result["subject"]
        except AIGenerationError as e:
            campaign.email_content = (
                f"<p>We couldn't generate content automatically: {e}</p>"
            )
        except Exception:
            # Catch-all so a stuck pill can never happen again — any unexpected
            # error still resolves the generating state instead of hanging forever.
            current_app.logger.exception("Unexpected error during AI generation")
            campaign.email_content = (
                "<p>Something went wrong generating this content. Please try again.</p>"
            )
        finally:
            campaign.is_generating = False
            db.session.commit()


def _start_generation(campaign):
    campaign.is_generating = True
    campaign.email_content = None
    db.session.commit()
    thread = threading.Thread(
        target=_run_generation, args=(app, campaign.id), daemon=True
    )
    thread.start()


def get_active_sender_emails():
    """Real, active sender identities from Brevo — using anything else gets rejected
    by Brevo with 'Sender is invalid / inactive' when creating/updating a campaign."""
    return [" info@businessplansite.com"]



def sync_contact_lists():
    """Upsert local ContactList rows from Brevo so EmailCampaign has real FKs to point at."""
    try:
        data = get_brevo_client().get_contact_lists(limit=50)
    except BrevoAPIError as e:
        app.logger.exception("Failed to fetch Brevo contact lists")
        if BREVO_STUB_MODE:
            lists = ContactList.query.all()
            if not lists:
                stub_list = ContactList(brevo_list_id=1, name="Test Email List (stub)")
                db.session.add(stub_list)
                db.session.commit()
                lists = [stub_list]
            return lists
        flash(
            _brevo_error_message(
                e,
                "Could not load contact lists from Brevo. Check the BREVO_API_KEY and try again.",
            ),
            "error",
        )
        return []
    lists = []
    for item in data.get("lists", []):
        brevo_list_id = item.get("id")
        contact_list = ContactList.query.filter_by(
            brevo_list_id=brevo_list_id
        ).one_or_none()
        if contact_list is None:
            contact_list = ContactList(
                brevo_list_id=brevo_list_id, name=item.get("name", "")
            )
            db.session.add(contact_list)
        else:
            contact_list.name = item.get("name", contact_list.name)
        lists.append(contact_list)
    db.session.commit()
    return lists


_brevo_client = None


def get_brevo_client():
    global _brevo_client
    if _brevo_client is None:
        _brevo_client = BrevoClient()
    return _brevo_client


def delete_brevo_campaign(brevo_campaign_id):
    """Delete a campaign on Brevo, working around Brevo's refusal to delete
    anything that was ever scheduled/sent (403 "Campaign once scheduled can
    not be deleted"): suspend it first (cancelling any pending send) and
    retry.

    Returns True if the campaign was actually deleted on Brevo, False if
    Brevo will never allow that (it already fully sent, so there's no
    pending send left to suspend either) — the caller should still remove
    the local record in that case, since nothing further is possible there.
    A 404 on the initial delete means it's already gone (also True). Any
    other failure propagates as BrevoAPIError for the caller to report.
    """
    brevo = get_brevo_client()
    try:
        brevo.delete_email_campaign(brevo_campaign_id)
        return True
    except BrevoAPIError as e:
        if e.status_code == 404:
            app.logger.info(
                "Brevo campaign %s already gone (404) — nothing to delete",
                brevo_campaign_id,
            )
            return True
        if e.status_code != 403:
            raise
        app.logger.info(
            "Brevo campaign %s blocked from deletion (403) — suspending and retrying",
            brevo_campaign_id,
        )

    try:
        brevo.update_campaign_status(brevo_campaign_id, "suspended")
    except BrevoAPIError:
        # Brevo also refuses to suspend a campaign that's already fully
        # sent (nothing pending left to cancel) — it's permanently stuck
        # there. Nothing more we can do on Brevo's side.
        app.logger.info(
            "Brevo campaign %s can't be suspended (already sent) — "
            "removing the local record only; it stays visible on Brevo.",
            brevo_campaign_id,
        )
        return False

    brevo.delete_email_campaign(brevo_campaign_id)
    return True


BREVO_BLACKLISTING_EVENTS = {
    "unsubscribe",
    "hardBounce",
    "blocked",
    "spam",
    "complaint",
}


def get_or_create_local_contact(email):
    normalized = Contact.normalize_email(email)
    contact = Contact.query.filter_by(email=normalized).one_or_none()
    if contact is None:
        contact = Contact(email=normalized)
        db.session.add(contact)
    return contact


@app.route("/webhooks/brevo/<secret>", methods=["POST"])
def brevo_webhook(secret):
    if not secrets.compare_digest(secret, app.config["BREVO_WEBHOOK_SECRET"] or ""):
        abort(404)

    payload = request.get_json(silent=True) or {}
    event = payload.get("event")
    email = payload.get("email")

    if not email:
        return "", 204

    contact = get_or_create_local_contact(email)
    contact.last_webhook_event = event
    if event in BREVO_BLACKLISTING_EVENTS:
        contact.is_blacklisted = True
    elif event == "click":
        link_path = urlsplit(payload.get("link") or "").path.rstrip("/")
        classification = FEEDBACK_LINK_CLASSIFICATIONS.get(link_path)
        if classification:
            contact.classification = classification
            contact.classified_at = utc_now()
    db.session.commit()

    return "", 204


def _record_feedback_click(classification):
    """Classify straight from the link's ?email= param instead of relying
    solely on Brevo's click-tracking webhook, which never fires for
    SMTP-delivered campaigns (no tracking layer wraps the link)."""
    email = request.args.get("email")
    if not email:
        return
    contact = get_or_create_local_contact(email)
    contact.classification = classification
    contact.classified_at = utc_now()
    db.session.commit()


@app.route("/feedback/interested")
def feedback_interested():
    _record_feedback_click(Contact.CLASSIFICATION_INTERESTED)
    return render_template(
        "feedback_response.html", message="Thanks, we'll be in touch."
    )


@app.route("/feedback/not-interested")
def feedback_not_interested():
    _record_feedback_click(Contact.CLASSIFICATION_NOT_INTERESTED)
    return render_template(
        "feedback_response.html", message="Got it — thanks for letting us know."
    )


# 1x1 transparent GIF, served for every open-tracking pixel request.
_TRACKING_PIXEL = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==")


@app.route("/track/open")
def track_open():
    campaign_id = request.args.get("campaign_id")
    email = request.args.get("email")
    if campaign_id and email:
        try:
            db.session.add(
                CampaignEngagement(
                    campaign_id=campaign_id,
                    contact_email=Contact.normalize_email(email),
                    event_type=CampaignEngagement.EVENT_OPEN,
                )
            )
            db.session.commit()
        except Exception:
            # A tampered/stale campaign_id shouldn't break pixel loading —
            # the recipient's inbox rendering must never depend on this.
            db.session.rollback()
            app.logger.exception("Failed to record open for campaign %s", campaign_id)
    return app.response_class(_TRACKING_PIXEL, mimetype="image/gif")


@app.route("/track/click")
def track_click():
    campaign_id = request.args.get("campaign_id")
    email = request.args.get("email")
    to = _normalize_cta_url(request.args.get("to") or "")
    if not to:
        abort(400)
    if campaign_id and email:
        try:
            db.session.add(
                CampaignEngagement(
                    campaign_id=campaign_id,
                    contact_email=Contact.normalize_email(email),
                    event_type=CampaignEngagement.EVENT_CLICK,
                    url=to,
                )
            )
            db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to record click for campaign %s", campaign_id)
    return redirect(to)


@app.route("/unsubscribe")
def unsubscribe():
    email = request.args.get("email")
    campaign_id = request.args.get("campaign_id")
    if email:
        contact = get_or_create_local_contact(email)
        contact.is_blacklisted = True
        contact.classification = Contact.CLASSIFICATION_UNSUBSCRIBED
        contact.classified_at = utc_now()
        if campaign_id:
            try:
                db.session.add(
                    CampaignEngagement(
                        campaign_id=campaign_id,
                        contact_email=Contact.normalize_email(email),
                        event_type=CampaignEngagement.EVENT_UNSUBSCRIBE,
                    )
                )
                db.session.commit()
            except Exception:
                # A tampered/stale campaign_id shouldn't block the
                # unsubscribe itself from taking effect.
                db.session.rollback()
                contact = get_or_create_local_contact(email)
                contact.is_blacklisted = True
                contact.classification = Contact.CLASSIFICATION_UNSUBSCRIBED
                contact.classified_at = utc_now()
                db.session.commit()
                app.logger.exception(
                    "Failed to record unsubscribe event for campaign %s", campaign_id
                )
        else:
            db.session.commit()
    return render_template(
        "feedback_response.html",
        message="You've been unsubscribed and won't receive further emails from us.",
    )


def _format_brevo_date(value):
    if not value:
        return "—"
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return f"{dt.month}/{dt.day}/{dt.year}"
    except ValueError:
        return value


def get_brevo_lists_detailed(client):
    """Return every contact list enriched with totalSubscribers/createdAt.

    The bulk GET /contacts/lists endpoint doesn't reliably include
    totalSubscribers and never includes createdAt — those only come back
    from the single-list endpoint, so each list is fetched individually.
    Reads are cached client-side, so this stays cheap on repeat page loads.
    """
    bulk = client.get_contact_lists(limit=50)
    detailed = []
    for item in bulk.get("lists", []):
        list_id = item.get("id")
        try:
            detailed.append(client.get_contact_list(list_id))
        except Exception:
            detailed.append(item)
    return detailed


def _contact_display_name(contact):
    attributes = contact.get("attributes") or {}
    name = " ".join(
        part
        for part in (attributes.get("FIRSTNAME"), attributes.get("LASTNAME"))
        if part
    ).strip()
    return name or contact.get("email", "")


def get_role_by_name(name):
    return Role.query.filter_by(name=name).one_or_none()


def get_or_create_role(name):
    role = get_role_by_name(name)
    if not role:
        role = Role(name=name)
        db.session.add(role)
    return role


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user.access_level != Role.NAME_ADMIN:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def create_password_setup_code(user):
    """Create a one-time password-setup token for the user. Returns the raw token."""
    token = secrets.token_urlsafe(32)
    expires_at = utc_now() + timedelta(hours=app.config["PASSWORD_SETUP_LINK_HOURS"])

    for active_code in PasswordResetCode.query.filter_by(user_id=user.id, used_at=None):
        active_code.mark_used()

    setup_code = PasswordResetCode(user=user, expires_at=expires_at)
    setup_code.set_code(token)
    db.session.add(setup_code)
    return token


@app.route("/")
def auth():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def index():
    stats = {"campaigns": 0, "contacts": 0, "unsubscribed": 0, "emails_sent": 0}
    recent_campaigns = []
    error = None
    try:
        client = get_brevo_client()

        campaigns_data = client.get_email_campaigns(limit=50)
        campaigns = campaigns_data.get("campaigns", [])
        stats["campaigns"] = campaigns_data.get("count", len(campaigns))

        for campaign in campaigns:
            g = (campaign.get("statistics") or {}).get("globalStats") or {}
            stats["unsubscribed"] += g.get("unsubscriptions", 0)
            stats["emails_sent"] += g.get("delivered", 0) or g.get("sent", 0)

        stats["contacts"] = sum(
            detail.get("totalSubscribers", 0)
            for detail in get_brevo_lists_detailed(client)
        )

        def _sort_key(c):
            return c.get("sentDate") or c.get("createdAt") or ""

        for campaign in sorted(campaigns, key=_sort_key, reverse=True)[:3]:
            g = (campaign.get("statistics") or {}).get("globalStats") or {}
            delivered = g.get("delivered", 0)
            opens = g.get("uniqueViews", 0)
            open_rate = round((opens / delivered) * 100) if delivered else 0
            recent_campaigns.append(
                {
                    "name": campaign.get("name"),
                    "status": (campaign.get("status") or "").capitalize() or "Draft",
                    "open_rate": f"{open_rate}%",
                    "subject": campaign.get("subject") or "",
                }
            )
    except Exception as e:
        app.logger.exception("Failed to fetch Brevo dashboard data.")
        error = _brevo_error_message(
            e,
            "Could not load live stats from Brevo. Check the BREVO_API_KEY and try again.",
        )

    return render_template(
        "index.html", stats=stats, recent_campaigns=recent_campaigns, error=error
    )


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        email = User.normalize_email(request.form.get("email"))
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")

        if not email:
            flash("Email is required.", "error")
            return render_template("signup.html", email=email), 400

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("signup.html", email=email), 400

        if not is_within_bcrypt_limit(password):
            flash("Password must be 72 bytes or fewer.", "error")
            return render_template("signup.html", email=email), 400

        if password != password_confirm:
            flash("Passwords do not match.", "error")
            return render_template("signup.html", email=email), 400

        if User.query.filter_by(email=email).first():
            flash("An account with this email already exists.", "error")
            return render_template("signup.html", email=email), 409

        admin_role = get_role_by_name(Role.NAME_ADMIN)
        if not admin_role:
            admin_role = Role(name=Role.NAME_ADMIN)
            db.session.add(admin_role)

        user = User(email=email, role=admin_role)
        user.set_password(password)

        db.session.add(user)

        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("An account with this email already exists.", "error")
            return render_template("signup.html", email=email), 409

        flash("Account created. Please log in.", "success")
        return redirect(url_for("login"))
    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        email = User.normalize_email(request.form.get("email"))
        password = request.form.get("password", "")
        next_url = request.form.get("next") or request.args.get("next")

        user = User.query.filter_by(email=email).first()

        if not user or not user.check_password(password):
            flash("Invalid email or password.", "error")
            return render_template("login.html", email=email, next_url=next_url), 401

        if not user.is_active:
            flash("This account is inactive. Please contact an administrator.", "error")
            return render_template("login.html", email=email, next_url=next_url), 403

        login_user(user)
        flash("Logged in successfully.", "success")

        if is_safe_redirect_url(next_url):
            return redirect(next_url)

        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        email = User.normalize_email(request.form.get("email"))
        code = (request.form.get("auth_code") or "").strip()
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")

        if not email or not code:
            flash("Email and reset code are required.", "error")
            return render_template("forgot_password.html", email=email), 400

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("forgot_password.html", email=email), 400

        if not is_within_bcrypt_limit(password):
            flash("Password must be 72 bytes or fewer.", "error")
            return render_template("forgot_password.html", email=email), 400

        if password != password_confirm:
            flash("Passwords do not match.", "error")
            return render_template("forgot_password.html", email=email), 400

        user = User.query.filter_by(email=email).first()
        reset_code = None

        if user:
            reset_code = (
                PasswordResetCode.query.filter_by(user_id=user.id, used_at=None)
                .order_by(PasswordResetCode.created_at.desc())
                .first()
            )

        if not user or not reset_code or not reset_code.is_valid_for(code):
            flash("Invalid or expired reset code.", "error")
            return render_template("forgot_password.html", email=email), 400

        user.set_password(password)
        reset_code.mark_used()
        db.session.commit()

        flash("Password reset successfully. Please log in.", "success")
        return redirect(url_for("login"))
    return render_template("forgot_password.html")


@app.post("/forgot-password/send-code")
def forgot_password_send_code():
    email = User.normalize_email(request.form.get("email"))

    if not email:
        return jsonify({"success": False, "message": "Email is required."}), 400

    user = User.query.filter_by(email=email).first()
    success_message = "If an account exists for that email, a reset code has been sent."

    if not user:
        return jsonify({"success": True, "message": success_message})

    now = utc_now()
    code = PasswordResetCode.generate_code()
    expires_at = now + timedelta(minutes=app.config["PASSWORD_RESET_CODE_MINUTES"])

    for active_code in PasswordResetCode.query.filter_by(user_id=user.id, used_at=None):
        active_code.mark_used()

    reset_code = PasswordResetCode(user=user, expires_at=expires_at)
    reset_code.set_code(code)
    db.session.add(reset_code)

    try:
        send_password_reset_email(user.email, code)
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("Failed to send password reset code.")
        return (
            jsonify(
                {
                    "success": False,
                    "message": "We could not send a reset code right now. Please try again.",
                }
            ),
            500,
        )

    return jsonify({"success": True, "message": success_message})


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


def _brevo_error_message(exc, default):
    if isinstance(exc, BrevoAPIError) and exc.status_code == 429:
        return "Brevo is rate-limiting requests right now. Please wait a minute and try again."
    return default


@app.route("/email-lists")
@login_required
def email_lists():
    lists = []
    error = None
    try:
        client = get_brevo_client()
        for detail in get_brevo_lists_detailed(client):
            lists.append(
                {
                    "id": detail.get("id"),
                    "name": detail.get("name"),
                    "contacts": detail.get("totalSubscribers", 0),
                    "date": _format_brevo_date(detail.get("createdAt")),
                }
            )
    except Exception as e:
        app.logger.exception("Failed to fetch Brevo contact lists.")
        error = _brevo_error_message(
            e,
            "Could not load email lists from Brevo. Check the BREVO_API_KEY and try again.",
        )
    return render_template("email_lists.html", lists=lists, error=error)


@app.route("/email-lists/<int:list_id>")
@login_required
def email_list_detail(list_id):
    list_name = f"List {list_id}"
    contacts = []
    error = None
    try:
        client = get_brevo_client()
        info = client.get_contact_list(list_id)
        list_name = info.get("name", list_name)
        data = client.get_contacts_from_list(list_id, limit=500)
        for contact in data.get("contacts", []):
            contacts.append(
                {
                    "name": _contact_display_name(contact),
                    "email": contact.get("email", ""),
                    "created": _format_brevo_date(contact.get("createdAt")),
                    "changed": _format_brevo_date(contact.get("modifiedAt")),
                    "blacklisted": bool(contact.get("emailBlacklisted")),
                }
            )
    except Exception as e:
        app.logger.exception("Failed to fetch Brevo list contacts.")
        error = _brevo_error_message(
            e,
            "Could not load this list from Brevo. Check the BREVO_API_KEY and try again.",
        )
    return render_template(
        "email_list_detail.html",
        list_id=list_id,
        list_name=list_name,
        contacts=contacts,
        error=error,
    )


@app.post("/email-lists/<int:list_id>/delete")
@login_required
def email_list_delete(list_id):
    try:
        get_brevo_client().delete_contact_list(list_id)
        flash("Email list deleted.", "success")
    except Exception:
        app.logger.exception("Failed to delete Brevo list.")
        flash("Could not delete this list on Brevo.", "error")
    return redirect(url_for("email_lists"))


@app.post("/email-lists/<int:list_id>/contacts/remove")
@login_required
def email_list_remove_contact(list_id):
    email = (request.form.get("email") or "").strip()
    if not email:
        flash("No email provided.", "error")
        return redirect(url_for("email_list_detail", list_id=list_id))
    try:
        get_brevo_client().remove_contacts_from_list(list_id, [email])
        flash(f"{email} removed from the list.", "success")
    except Exception:
        app.logger.exception("Failed to remove contact from Brevo list.")
        flash("Could not remove this contact on Brevo.", "error")
    return redirect(url_for("email_list_detail", list_id=list_id))


@app.route("/email-lists/<int:list_id>/export")
@login_required
def email_list_export(list_id):
    import csv
    import io

    try:
        client = get_brevo_client()
        info = client.get_contact_list(list_id)
        data = client.get_contacts_from_list(list_id, limit=500)
    except Exception:
        app.logger.exception("Failed to export Brevo list.")
        flash("Could not export this list from Brevo.", "error")
        return redirect(url_for("email_lists"))

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["Contact Name", "Email", "Creation Date", "Last Changed", "Blacklisted"]
    )
    for contact in data.get("contacts", []):
        writer.writerow(
            [
                _contact_display_name(contact),
                contact.get("email", ""),
                _format_brevo_date(contact.get("createdAt")),
                _format_brevo_date(contact.get("modifiedAt")),
                "Yes" if contact.get("emailBlacklisted") else "No",
            ]
        )

    filename = secure_filename(info.get("name", f"list-{list_id}")) or f"list-{list_id}"
    return app.response_class(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}.csv"},
    )


@app.route("/campaign-manager")
@login_required
def campaign_manager():
    campaigns = (
        EmailCampaign.query.filter(EmailCampaign.brevo_campaign_id.isnot(None))
        .order_by(EmailCampaign.created_at.desc())
        .all()
    )

    rows = []
    for campaign in campaigns:
        opens, clicks, unsubs, date = "N/A", "N/A", 0, "—"
        try:
            report = get_brevo_client().get_campaign_report(campaign.brevo_campaign_id)
            stats = _campaign_stats(report)
            opens, clicks, unsubs, date = (
                stats["opens"],
                stats["clicks"],
                stats["unsubs"],
                stats["date"],
            )
        except Exception:
            app.logger.exception(
                "Failed to fetch Brevo stats for campaign %s", campaign.id
            )

        smtp_stats = _smtp_engagement_stats(campaign.id)
        if smtp_stats:
            opens, clicks, unsubs = (
                smtp_stats["opens"],
                smtp_stats["clicks"],
                smtp_stats["unsubs"],
            )

        rows.append(
            {
                "id": campaign.id,
                "name": campaign.subject or "Untitled campaign",
                "list": campaign.contact_list.name if campaign.contact_list else "—",
                "opens": opens,
                "clicks": clicks,
                "unsub": unsubs,
                "date": date,
                "scheduled": campaign.status == EmailCampaign.STATUS_SCHEDULED,
                "status": campaign.status.capitalize(),
                "quarterly_active": campaign.recurrence_rule == EmailCampaign.RECURRENCE_QUARTERLY,
                "quarterly_next_run": (
                    campaign.next_run_at.strftime("%b %d, %Y")
                    if campaign.next_run_at
                    else None
                ),
            }
        )

    return render_template("campaign_manager.html", campaigns=rows)


@app.post("/campaign-manager/<campaign_id>/quarterly-toggle")
@login_required
def campaign_manager_quarterly_toggle(campaign_id):
    campaign = db.session.get(EmailCampaign, campaign_id)
    if not campaign:
        abort(404)

    if campaign.recurrence_rule == EmailCampaign.RECURRENCE_QUARTERLY:
        campaign.recurrence_rule = None
        campaign.next_run_at = None
        _cancel_quarterly_timer(campaign.id)
        flash("Quarterly run stopped.", "success")
    else:
        campaign.recurrence_rule = EmailCampaign.RECURRENCE_QUARTERLY
        campaign.next_run_at = _first_quarterly_run_at(utc_now())
        _schedule_quarterly_timer(campaign.id, campaign.next_run_at)
        flash(
            f"Quarterly run activated — next send {campaign.next_run_at:%b %d, %Y}.",
            "success",
        )

    db.session.commit()
    return redirect(url_for("campaign_manager"))


@app.post("/campaign-manager/<campaign_id>/delete")
@login_required
def campaign_manager_delete(campaign_id):
    campaign = db.session.get(EmailCampaign, campaign_id)
    if not campaign:
        abort(404)

    deleted_on_brevo = True
    if campaign.brevo_campaign_id:
        try:
            deleted_on_brevo = delete_brevo_campaign(campaign.brevo_campaign_id)
        except BrevoAPIError as e:
            app.logger.exception(
                "Failed to delete Brevo campaign %s", campaign.brevo_campaign_id
            )
            flash(f"Could not delete this campaign on Brevo: {e}", "error")
            return redirect(url_for("campaign_manager"))

    db.session.delete(campaign)
    db.session.commit()
    if deleted_on_brevo:
        flash("Campaign deleted.", "success")
    else:
        flash(
            "Removed from this list. This campaign had already fully sent, so "
            "Brevo doesn't allow deleting it there — it still exists in your "
            "Brevo dashboard.",
            "success",
        )
    return redirect(url_for("campaign_manager"))


@app.route("/campaign-manager/wizard/<mode>", methods=["GET", "POST"])
@login_required
def campaign_wizard(mode):
    if mode not in EmailCampaign.MODES:
        abort(404)

    if request.method == "POST":
        content_file = request.files.get("content_file")
        logo_file = request.files.get("logo_file")
        cta_links = request.form.get("cta_links", "")

        if not content_file or content_file.filename == "":
            return jsonify({"error": "Content file is required."}), 400

        if not _allowed_file(content_file.filename, ALLOWED_CONTENT_EXTENSIONS):
            return jsonify({"error": "Unsupported content file type."}), 400

        content_bytes = content_file.read()
        if len(content_bytes) > MAX_UPLOAD_SIZE:
            return jsonify({"error": "Content file exceeds 5MB limit."}), 400

        try:
            extracted_text = extract_text(content_bytes, content_file.filename)
        except ExtractionError as e:
            return jsonify({"error": str(e)}), 400

        campaign = _get_or_create_campaign(mode)
        campaign.content_file_name = secure_filename(content_file.filename)
        campaign.extracted_text = extracted_text
        campaign.cta_links = cta_links
        # Reset any previously generated (or failed) content so the Template step
        # regenerates fresh from the new upload instead of reusing stale content —
        # including a stale error message from a prior failed AI generation attempt.
        campaign.email_content = None
        campaign.is_generating = False

        if logo_file and logo_file.filename != "":
            if not _allowed_file(logo_file.filename, ALLOWED_LOGO_EXTENSIONS):
                return jsonify({"error": "Unsupported logo file type."}), 400

            logo_bytes = logo_file.read()
            if len(logo_bytes) > MAX_UPLOAD_SIZE:
                return jsonify({"error": "Logo file exceeds 5MB limit."}), 400

            os.makedirs(LOGO_UPLOAD_DIR, exist_ok=True)
            ext = logo_file.filename.rsplit(".", 1)[1].lower()
            logo_filename = f"{campaign.id}.{ext}"
            with open(os.path.join(LOGO_UPLOAD_DIR, logo_filename), "wb") as f:
                f.write(logo_bytes)

            campaign.logo_url = url_for(
                "static", filename=f"uploads/logos/{logo_filename}"
            )

        db.session.commit()

        return jsonify(
            {"success": True, "next_url": url_for("wizard_template", mode=mode)}
        )

    return render_template("wizard_setup.html", mode=mode)


@app.route("/campaign-manager/wizard/<mode>/template", methods=["GET"])
@login_required
def wizard_template(mode):
    if mode not in EmailCampaign.MODES:
        abort(404)

    campaign = get_current_campaign()
    if not campaign or not campaign.extracted_text:
        return redirect(url_for("campaign_wizard", mode=mode))

    if (
        mode == EmailCampaign.MODE_AI
        and not campaign.email_content
        and not campaign.is_generating
    ):
        _start_generation(campaign)
    elif mode == EmailCampaign.MODE_MANUAL and campaign.email_content is None:
        cta_buttons = [
            {"url": url, "label": "Learn More"}
            for url in _parse_cta_links(campaign.cta_links)
        ]
        campaign.email_content = (
            _add_inline_spacing(f"<p>{campaign.extracted_text}</p>")
            + _cta_buttons_html(cta_buttons)
            + _feedback_footer_html()
        )
        campaign.subject = campaign.subject or _default_subject_from_filename(
            campaign.content_file_name
        )
        db.session.commit()

    contact_lists = sync_contact_lists()

    return render_template(
        "wizard_template.html",
        mode=mode,
        campaign=campaign,
        is_generating=campaign.is_generating,
        sender_emails=get_active_sender_emails(),
        sender_name=campaign.sender_name or "Larhdel Law",
        email_subject=campaign.subject or "",
        email_lists=contact_lists,
        selected_email_list_id=campaign.contact_list_id,
        email_content=campaign.email_content,
        logo_url=campaign.logo_url,
    )


@app.route("/campaign-manager/wizard/<mode>/template/status")
@login_required
def wizard_template_status(mode):
    campaign = get_current_campaign()
    if not campaign:
        return jsonify({"error": "No campaign found"}), 404
    return jsonify(
        {
            "is_generating": campaign.is_generating,
            "email_content": campaign.email_content,
            "email_subject": campaign.subject,
        }
    )


@app.route("/campaign-manager/wizard/<mode>/template/save", methods=["POST"])
@login_required
def wizard_template_save(mode):
    if mode not in EmailCampaign.MODES:
        abort(404)

    campaign = get_current_campaign()
    if not campaign:
        return jsonify({"error": "No campaign found"}), 404

    payload = request.get_json(silent=True) or {}
    if "email_content" in payload:
        campaign.email_content = payload["email_content"]
        db.session.commit()
        return jsonify({"success": True})

    if campaign.is_generating:
        return (
            jsonify(
                {"error": "Content is still generating — please wait for it to finish."}
            ),
            409,
        )

    campaign.sender_email = payload.get("sender_email") or campaign.sender_email
    campaign.sender_name = payload.get("sender_name") or campaign.sender_name
    campaign.subject = payload.get("email_subject") or campaign.subject

    contact_list_id = payload.get("email_list")
    if contact_list_id:
        campaign.contact_list_id = contact_list_id

    contact_list = (
        db.session.get(ContactList, campaign.contact_list_id)
        if campaign.contact_list_id
        else None
    )
    list_ids = [contact_list.brevo_list_id] if contact_list else None

    campaign_kwargs = dict(
        name=f"{campaign.subject or 'Untitled campaign'} ({mode})",
        subject=campaign.subject,
        sender_name=campaign.sender_name,
        sender_email=campaign.sender_email,
        html_content=_compose_send_html(campaign),
        list_ids=list_ids,
    )

    if BREVO_STUB_MODE:
        campaign.brevo_campaign_id = campaign.brevo_campaign_id or f"stub-{campaign.id}"
    else:
        brevo = get_brevo_client()
        try:
            if campaign.brevo_campaign_id:
                brevo.update_email_campaign(campaign.brevo_campaign_id, **campaign_kwargs)
            else:
                result = brevo.create_email_campaign(**campaign_kwargs)
                campaign.brevo_campaign_id = result.get("id")
        except BrevoAPIError as e:
            db.session.rollback()
            return jsonify({"error": str(e)}), 502

    campaign.status = EmailCampaign.STATUS_READY
    db.session.commit()

    return jsonify({"success": True, "next_url": url_for("wizard_test_send", mode=mode)})


@app.route("/campaign-manager/wizard/<mode>/test-send")
@login_required
def wizard_test_send(mode):
    if mode not in EmailCampaign.MODES:
        abort(404)

    campaign = get_current_campaign()
    if not campaign or not campaign.brevo_campaign_id:
        return redirect(url_for("wizard_template", mode=mode))

    test_emails = TestEmail.query.order_by(TestEmail.created_at.asc()).all()

    return render_template(
        "wizard_test_send.html",
        mode=mode,
        campaign=campaign,
        email_content=campaign.email_content,
        logo_url=campaign.logo_url,
        test_emails=test_emails,
    )


@app.route("/campaign-manager/wizard/<mode>/test-send/send", methods=["POST"])
@login_required
def wizard_test_send_action(mode):
    campaign = get_current_campaign()
    if not campaign or not campaign.brevo_campaign_id:
        return jsonify({"error": "No campaign found"}), 404

    payload = request.get_json(silent=True) or {}
    emails = [e for e in (payload.get("emails") or []) if e]
    if not emails:
        return jsonify({"error": "Select at least one test email."}), 400

    if BREVO_STUB_MODE:
        return jsonify({"success": True})
    try:
        get_brevo_client().send_test_email(campaign.brevo_campaign_id, emails)
    except BrevoAPIError as e:
        return jsonify({"error": str(e)}), 502

    return jsonify({"success": True})


@app.route("/test-emails")
@login_required
def test_emails_page():
    contact_lists = sync_contact_lists()
    requested_list_id = request.args.get("list_id")
    selected_list = (
        db.session.get(ContactList, requested_list_id)
        if requested_list_id
        else next(
            (cl for cl in contact_lists if "test" in cl.name.strip().lower()),
            (contact_lists[0] if contact_lists else None),
        )
    )

    brevo_contacts = []
    error = None
    if selected_list:
        try:
            existing_emails = {te.email for te in TestEmail.query.all()}
            data = get_brevo_client().get_contacts_from_list(
                selected_list.brevo_list_id, limit=500
            )
            for brevo_contact in data.get("contacts", []):
                email = brevo_contact.get("email")
                if not email:
                    continue
                brevo_contacts.append(
                    {
                        "name": _contact_display_name(brevo_contact),
                        "email": email,
                        "already_added": TestEmail.normalize_email(email)
                        in existing_emails,
                    }
                )
        except Exception as e:
            app.logger.exception("Failed to fetch contacts for test-email picker.")
            error = _brevo_error_message(
                e,
                "Could not load contacts from Brevo. Check the BREVO_API_KEY and try again.",
            )
    else:
        error = "No Brevo contact lists found."

    return render_template(
        "test_emails.html",
        contact_lists=contact_lists,
        selected_list=selected_list,
        brevo_contacts=brevo_contacts,
        error=error,
    )


@app.post("/test-emails/save")
@login_required
def test_emails_page_save():
    selected = request.form.getlist("emails")
    name_by_email = {}
    for entry in selected:
        email, _, name = entry.partition("|")
        name_by_email[TestEmail.normalize_email(email)] = name or None

    added = 0
    for email, name in name_by_email.items():
        if not email or TestEmail.query.filter_by(email=email).first():
            continue
        db.session.add(TestEmail(email=email, name=name, added_by_id=current_user.id))
        added += 1
    db.session.commit()

    flash(
        f"Added {added} test email(s)." if added else "No new test emails to add.",
        "success",
    )
    return redirect(url_for("test_emails_page", list_id=request.form.get("list_id")))


@app.post("/test-emails/<test_email_id>/remove")
@login_required
def test_emails_page_remove(test_email_id):
    test_email = db.session.get(TestEmail, test_email_id)
    if test_email:
        db.session.delete(test_email)
        db.session.commit()
        flash("Test email removed.", "success")
    return redirect(url_for("test_emails_page"))


@app.route("/campaign-manager/wizard/<mode>/schedule", methods=["GET"])
@login_required
def wizard_schedule(mode):
    if mode not in EmailCampaign.MODES:
        abort(404)

    campaign = get_current_campaign()
    if not campaign or not campaign.brevo_campaign_id:
        return redirect(url_for("wizard_template", mode=mode))

    return render_template(
        "wizard_schedule.html",
        mode=mode,
        campaign=campaign,
        sender_emails=(
            [campaign.sender_email]
            if campaign.sender_email
            else get_active_sender_emails()
        ),
        sender_name=campaign.sender_name,
        email_subject=campaign.subject,
        email_lists=[campaign.contact_list] if campaign.contact_list else [],
        email_content=campaign.email_content,
        logo_url=campaign.logo_url,
    )


@app.route("/campaign-manager/wizard/<mode>/schedule/action", methods=["POST"])
@login_required
def wizard_schedule_action(mode):
    campaign = get_current_campaign()
    if not campaign or not campaign.brevo_campaign_id:
        return jsonify({"error": "No campaign found"}), 404

    payload = request.get_json(silent=True) or {}
    action = payload.get("action")
    use_smtp = app.config["CAMPAIGN_DELIVERY_PROVIDER"] == "smtp"
    brevo = None if (BREVO_STUB_MODE or use_smtp) else get_brevo_client()

    try:
        if action == "run_now":
            if use_smtp:
                sent, failed = send_campaign_via_smtp(campaign)
                if sent == 0 and failed > 0:
                    return (
                        jsonify(
                            {
                                "error": f"SMTP send failed for all {failed} recipient(s) — check the logs."
                            }
                        ),
                        502,
                    )
                app.logger.info(
                    "Campaign %s sent over SMTP: %s sent, %s failed",
                    campaign.id,
                    sent,
                    failed,
                )
            elif not BREVO_STUB_MODE:
                brevo.update_email_campaign(
                    campaign.brevo_campaign_id, html_content=_compose_send_html(campaign)
                )
                brevo.send_campaign_now(campaign.brevo_campaign_id)
            campaign.status = EmailCampaign.STATUS_SENT
            db.session.add(
                CampaignRun(
                    campaign_id=campaign.id,
                    brevo_campaign_id=campaign.brevo_campaign_id,
                    run_at=utc_now(),
                    status=CampaignRun.STATUS_SENT,
                )
            )
        elif action == "schedule":
            scheduled_at = payload.get("scheduled_at")
            if not scheduled_at:
                return jsonify({"error": "A schedule date/time is required."}), 400
            # The browser sends an absolute UTC instant (see wizard_schedule.js),
            # so this is tz-aware already — no need (or ability) to guess whose
            # timezone a naive string would have meant.
            try:
                aware_dt = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
            except ValueError:
                return jsonify({"error": "Invalid schedule date/time."}), 400
            if aware_dt.tzinfo is None:
                aware_dt = aware_dt.replace(tzinfo=timezone.utc)
            campaign.scheduled_at = aware_dt.astimezone(timezone.utc).replace(tzinfo=None)
            campaign.status = EmailCampaign.STATUS_SCHEDULED
            if use_smtp:
                # Fires in-process at aware_dt — see _schedule_smtp_campaign's
                # docstring for the "lost on restart" caveat.
                _schedule_smtp_campaign(campaign, aware_dt)
            elif not BREVO_STUB_MODE:
                brevo.update_email_campaign(
                    campaign.brevo_campaign_id, scheduled_at=aware_dt.isoformat()
                )
            db.session.add(
                CampaignRun(
                    campaign_id=campaign.id,
                    brevo_campaign_id=campaign.brevo_campaign_id,
                    run_at=campaign.scheduled_at,
                    status=CampaignRun.STATUS_SCHEDULED,
                )
            )
        elif action == "finish":
            pass
        else:
            return jsonify({"error": "Unknown action."}), 400
    except BrevoAPIError as e:
        return jsonify({"error": str(e)}), 502

    db.session.commit()
    session.pop("campaign_id", None)

    return jsonify({"success": True, "next_url": url_for("campaign_manager")})


@app.route("/campaign-manager/<campaign_id>")
@login_required
def campaign_recipients(campaign_id):
    campaign = db.session.get(EmailCampaign, campaign_id)
    if not campaign:
        abort(404)

    contacts = []
    error = None
    if campaign.contact_list:
        try:
            data = get_brevo_client().get_contacts_from_list(
                campaign.contact_list.brevo_list_id, limit=500
            )
            for brevo_contact in data.get("contacts", []):
                email = brevo_contact.get("email", "")
                normalized_email = Contact.normalize_email(email)
                local_contact = Contact.query.filter_by(
                    email=normalized_email
                ).one_or_none()
                if local_contact is None:
                    local_contact = Contact(
                        email=normalized_email,
                        is_blacklisted=bool(brevo_contact.get("emailBlacklisted")),
                    )
                    db.session.add(local_contact)
                contacts.append(
                    {
                        "name": _contact_display_name(brevo_contact),
                        "email": email,
                        "created": _format_brevo_date(brevo_contact.get("createdAt")),
                        "changed": _format_brevo_date(brevo_contact.get("modifiedAt")),
                        "classification": local_contact.classification,
                        "blacklisted": local_contact.is_blacklisted,
                    }
                )
            db.session.commit()
        except Exception as e:
            app.logger.exception(
                "Failed to fetch recipients for campaign %s", campaign_id
            )
            error = _brevo_error_message(
                e,
                "Could not load recipients from Brevo. Check the BREVO_API_KEY and try again.",
            )

    return render_template(
        "campaign_recipients.html",
        campaign_id=campaign_id,
        contacts=contacts,
        error=error,
        classifications=Contact.CLASSIFICATIONS,
    )


@app.post("/campaign-manager/contacts/classification")
@login_required
def set_contact_classification():
    payload = request.get_json(silent=True) or {}
    email = payload.get("email")
    classification = payload.get("classification") or None

    if not email:
        return jsonify({"error": "Email is required."}), 400
    if classification and classification not in Contact.CLASSIFICATIONS:
        return jsonify({"error": "Invalid classification."}), 400

    contact = get_or_create_local_contact(email)
    contact.classification = classification
    contact.classified_by_id = current_user.id
    contact.classified_at = utc_now()
    db.session.commit()

    try:
        get_brevo_client().update_contact_attributes(
            contact.email, {"attributes": {"CLASSIFICATION": classification}}
        )
    except BrevoAPIError:
        app.logger.exception(
            "Failed to push classification to Brevo for %s", contact.email
        )

    return jsonify({"success": True})


@app.route("/campaign-manager/<campaign_id>/preview")
@login_required
def campaign_preview(campaign_id):
    campaign = db.session.get(EmailCampaign, campaign_id)
    if not campaign:
        abort(404)

    return render_template(
        "campaign_preview.html",
        campaign_id=campaign_id,
        campaign=campaign,
        email_content=campaign.email_content,
        logo_url=campaign.logo_url,
        sender_emails=(
            [campaign.sender_email]
            if campaign.sender_email
            else get_active_sender_emails()
        ),
        sender_name=campaign.sender_name,
        email_subject=campaign.subject,
        email_lists=[campaign.contact_list] if campaign.contact_list else [],
    )


def _campaign_stats(campaign):
    stats = (campaign.get("statistics") or {}).get("globalStats") or {}
    return {
        "id": campaign.get("id"),
        "name": campaign.get("name"),
        "opens": stats.get("uniqueViews", 0),
        "clicks": stats.get("uniqueClicks", 0),
        "unsubs": stats.get("unsubscriptions", 0),
        "date": _format_brevo_date(campaign.get("createdAt")),
    }


def _smtp_engagement_stats(campaign_id):
    """Unique opens/clicks/unsubscribes tracked ourselves for a
    SMTP-delivered campaign (Brevo's own stats stay at 0 for these, since
    Brevo never sent them). None, None, None if nothing's been tracked —
    callers should fall back to Brevo's numbers in that case, since a given
    campaign_id could in principle have been sent under either provider."""
    opens = (
        db.session.query(CampaignEngagement.contact_email)
        .filter_by(campaign_id=campaign_id, event_type=CampaignEngagement.EVENT_OPEN)
        .distinct()
        .count()
    )
    clicks = (
        db.session.query(CampaignEngagement.contact_email)
        .filter_by(campaign_id=campaign_id, event_type=CampaignEngagement.EVENT_CLICK)
        .distinct()
        .count()
    )
    unsubs = (
        db.session.query(CampaignEngagement.contact_email)
        .filter_by(campaign_id=campaign_id, event_type=CampaignEngagement.EVENT_UNSUBSCRIBE)
        .distinct()
        .count()
    )
    if not opens and not clicks and not unsubs:
        return None
    return {"opens": opens, "clicks": clicks, "unsubs": unsubs}


@app.route("/reports")
@login_required
def reports():
    campaigns = []
    error = None
    try:
        data = get_brevo_client().get_email_campaigns(limit=50)
        for c in data.get("campaigns", []):
            stats = _campaign_stats(c)
            local_campaign = EmailCampaign.query.filter_by(
                brevo_campaign_id=str(c.get("id"))
            ).one_or_none()
            smtp_stats = (
                _smtp_engagement_stats(local_campaign.id) if local_campaign else None
            )
            if smtp_stats:
                stats["opens"] = smtp_stats["opens"]
                stats["clicks"] = smtp_stats["clicks"]
                stats["unsubs"] = smtp_stats["unsubs"]
            campaigns.append(stats)
    except Exception as e:
        app.logger.exception("Failed to fetch Brevo campaigns.")
        error = _brevo_error_message(
            e,
            "Could not load reports from Brevo. Check the BREVO_API_KEY and try again.",
        )
    return render_template("reports.html", campaigns=campaigns, error=error)


@app.post("/reports/<int:campaign_id>/delete")
@login_required
def report_delete(campaign_id):
    try:
        deleted_on_brevo = delete_brevo_campaign(campaign_id)
        if deleted_on_brevo:
            flash("Campaign deleted.", "success")
        else:
            flash(
                "This campaign had already fully sent, so Brevo doesn't allow "
                "deleting it there — it still exists in your Brevo dashboard.",
                "success",
            )
    except Exception as e:
        app.logger.exception("Failed to delete Brevo campaign %s", campaign_id)
        detail = str(e) if isinstance(e, BrevoAPIError) else "Unexpected error — check the logs."
        flash(f"Could not delete this campaign on Brevo: {detail}", "error")
    return redirect(url_for("reports"))


@app.route("/reports/<int:campaign_id>/export")
@login_required
def report_export(campaign_id):
    import csv
    import io

    try:
        campaign = get_brevo_client().get_campaign_report(campaign_id)
    except Exception:
        app.logger.exception("Failed to export Brevo campaign report.")
        flash("Could not export this report from Brevo.", "error")
        return redirect(url_for("reports"))

    row = _campaign_stats(campaign)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["Campaign Name", "Opens", "Clicks", "Unsubscribes", "Creation Date"]
    )
    writer.writerow(
        [row["name"], row["opens"], row["clicks"], row["unsubs"], row["date"]]
    )

    filename = (
        secure_filename(row["name"] or f"campaign-{campaign_id}")
        or f"campaign-{campaign_id}"
    )
    return app.response_class(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}-report.csv"},
    )


@app.route("/user-accounts")
@login_required
@admin_required
def user_accounts():
    users = User.query.order_by(User.created_at.asc()).all()
    return render_template("user_accounts.html", users=users)


@app.route("/user-accounts/edit", methods=["GET", "POST"])
@login_required
@admin_required
def user_form():
    roles = Role.query.order_by(Role.name).all()
    user = None
    user_id = request.values.get("user_id")
    if user_id:
        user = db.session.get(User, user_id)
        if not user:
            abort(404)

    if request.method == "POST":
        email = User.normalize_email(request.form.get("email"))
        first_name = (request.form.get("first_name") or "").strip()
        last_name = (request.form.get("last_name") or "").strip()
        contact_number = (request.form.get("contact_number") or "").strip()
        role_id = request.form.get("role_id")

        if not email:
            flash("Email is required.", "error")
            return render_template("user_form.html", roles=roles, user=user), 400

        existing = User.query.filter_by(email=email).first()
        if existing and (not user or existing.id != user.id):
            flash("An account with this email already exists.", "error")
            return render_template("user_form.html", roles=roles, user=user), 409

        role = db.session.get(Role, role_id) if role_id else None
        if not role:
            role = get_or_create_role(Role.NAME_STAFF)

        if user:
            user.email = email
            user.first_name = first_name
            user.last_name = last_name
            user.contact_number = contact_number
            user.role = role
            db.session.commit()
            flash("User updated.", "success")
            return redirect(url_for("user_accounts"))

        new_user = User(
            email=email,
            first_name=first_name,
            last_name=last_name,
            contact_number=contact_number,
            role=role,
        )
        # Placeholder credential — the real password is chosen by the user
        # through the emailed setup link, so this value must be unguessable.
        new_user.set_password(secrets.token_urlsafe(32))
        db.session.add(new_user)
        db.session.flush()

        token = create_password_setup_code(new_user)
        setup_link = url_for(
            "setup_password", user_id=new_user.id, token=token, _external=True
        )

        try:
            send_password_setup_email(email, setup_link)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("An account with this email already exists.", "error")
            return render_template("user_form.html", roles=roles, user=user), 409
        except Exception:
            db.session.rollback()
            app.logger.exception("Failed to send password setup email.")
            flash(
                "We could not send the setup email, so the account was not created. Please try again.",
                "error",
            )
            return render_template("user_form.html", roles=roles, user=user), 500

        flash(
            f"Account created. A password setup link was emailed to {email}.", "success"
        )
        return redirect(url_for("user_accounts"))

    return render_template("user_form.html", roles=roles, user=user)


@app.post("/user-accounts/<user_id>/delete")
@login_required
@admin_required
def user_delete(user_id):
    user = db.session.get(User, user_id)
    if not user:
        abort(404)

    if user.id == current_user.id:
        flash("You cannot delete your own account.", "error")
        return redirect(url_for("user_accounts"))

    PasswordResetCode.query.filter_by(user_id=user.id).delete()
    db.session.delete(user)
    db.session.commit()
    flash("User deleted.", "success")
    return redirect(url_for("user_accounts"))


@app.route("/setup-password/<user_id>", methods=["GET", "POST"])
def setup_password(user_id):
    # The link may be opened while someone else (e.g. the admin who created
    # the account) is logged in on this browser — end that session so the
    # invited user always lands on the password form.
    if current_user.is_authenticated:
        logout_user()

    token = (request.values.get("token") or "").strip()
    user = db.session.get(User, user_id)

    setup_code = None
    if user and token:
        setup_code = (
            PasswordResetCode.query.filter_by(user_id=user.id, used_at=None)
            .order_by(PasswordResetCode.created_at.desc())
            .first()
        )

    if not user or not setup_code or not setup_code.is_valid_for(token):
        flash(
            "This password setup link is invalid or has expired. Please contact an administrator.",
            "error",
        )
        return redirect(url_for("login"))

    if request.method == "POST":
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("setup_password.html", user=user, token=token), 400

        if not is_within_bcrypt_limit(password):
            flash("Password must be 72 bytes or fewer.", "error")
            return render_template("setup_password.html", user=user, token=token), 400

        if password != password_confirm:
            flash("Passwords do not match.", "error")
            return render_template("setup_password.html", user=user, token=token), 400

        user.set_password(password)
        setup_code.mark_used()
        db.session.commit()

        flash("Password set successfully. You can now log in.", "success")
        return redirect(url_for("login"))

    return render_template("setup_password.html", user=user, token=token)


if __name__ == "__main__":
    # Werkzeug's reloader (debug=True, on by default) re-execs this module
    # in a child process with WERKZEUG_RUN_MAIN=true; the parent watcher
    # process never actually serves requests, so only arm timers in the
    # child — otherwise every reload would double-schedule them.
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        _rearm_quarterly_campaigns()
    app.run(debug=True)
