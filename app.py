from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlsplit

import os
import secrets
import threading
from flask import Flask, abort, flash, current_app, redirect, render_template, request, url_for, jsonify, session
from werkzeug.utils import secure_filename
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import IntegrityError

from config import Config
from extensions import db, login_manager, migrate
from logging_config import setup_logging
from models import CampaignRun, Contact, ContactList, EmailCampaign, PasswordResetCode, Role, TestEmail, User, utc_now
from services.email_service import (
    send_password_reset_email,
    send_password_setup_email,
)
from utils.security import BCRYPT_MAX_BYTES
from tools.brevo import BrevoAPIError, BrevoClient
from services.document_extractor import extract_text, ExtractionError
from services.ai_email_content import generate_email_content, AIGenerationError

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
    return parsed_target.scheme == "" and parsed_target.netloc == "" and target.startswith("/")


def is_within_bcrypt_limit(value):
    return len((value or "").encode("utf-8")) <= BCRYPT_MAX_BYTES

ALLOWED_CONTENT_EXTENSIONS = {"doc", "docx", "pdf"}
ALLOWED_LOGO_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # Max size: 5MB
LOGO_UPLOAD_DIR = os.path.join(app.static_folder, "uploads", "logos")
os.makedirs(LOGO_UPLOAD_DIR, exist_ok=True)

def _allowed_file(filename, allowed_extensions):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in allowed_extensions
    )

_ACTIVE_CAMPAIGN_STATUSES = (
    EmailCampaign.STATUS_DRAFT,
    EmailCampaign.STATUS_GENERATING,
    EmailCampaign.STATUS_READY,
)


def _get_or_create_campaign(mode):
    campaign_id = session.get("campaign_id")
    campaign = db.session.get(EmailCampaign, campaign_id) if campaign_id else None
    if campaign is None or campaign.status not in _ACTIVE_CAMPAIGN_STATUSES:
        campaign = EmailCampaign(created_by_id=current_user.id, mode=mode, status=EmailCampaign.STATUS_DRAFT)
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
    return (
        '<div style="margin-top:24px;padding-top:16px;border-top:1px solid #e5e7eb;">'
        "<p>Still interested in hearing from us?</p>"
        f'<a href="{base}/feedback/interested">Yes, I\'m interested</a>'
        "&nbsp;|&nbsp;"
        f'<a href="{base}/feedback/not-interested">Not right now</a>'
        "</div>"
    )


def _run_generation(app_ref, campaign_id):
    with app_ref.app_context():
        campaign = db.session.get(EmailCampaign, campaign_id)
        if not campaign:
            return
        try:
            content = generate_email_content(
                extracted_text=campaign.extracted_text,
                cta_links=campaign.cta_links or "",
                sender_name=campaign.sender_name or "Larhdel Law",
            )
            campaign.email_content = content + _feedback_footer_html()
        except AIGenerationError as e:
            campaign.email_content = f"<p>We couldn't generate content automatically: {e}</p>"
        except Exception:
            # Catch-all so a stuck pill can never happen again — any unexpected
            # error still resolves the generating state instead of hanging forever.
            current_app.logger.exception("Unexpected error during AI generation")
            campaign.email_content = "<p>Something went wrong generating this content. Please try again.</p>"
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


def sync_contact_lists():
    """Upsert local ContactList rows from Brevo so EmailCampaign has real FKs to point at."""
    data = get_brevo_client().get_contact_lists(limit=50)
    lists = []
    for item in data.get("lists", []):
        brevo_list_id = item.get("id")
        contact_list = ContactList.query.filter_by(brevo_list_id=brevo_list_id).one_or_none()
        if contact_list is None:
            contact_list = ContactList(brevo_list_id=brevo_list_id, name=item.get("name", ""))
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


BREVO_BLACKLISTING_EVENTS = {"unsubscribe", "hardBounce", "blocked", "spam", "complaint"}


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


@app.route("/feedback/interested")
def feedback_interested():
    return render_template("feedback_response.html", message="Thanks, we'll be in touch.")


@app.route("/feedback/not-interested")
def feedback_not_interested():
    return render_template("feedback_response.html", message="Got it — thanks for letting us know.")


def _format_brevo_date(value):
    if not value:
        return "—"
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return f"{dt.month}/{dt.day}/{dt.year}"
    except ValueError:
        return value


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
    return render_template("index.html")


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
        return jsonify(
            {
                "success": False,
                "message": "We could not send a reset code right now. Please try again.",
            }
        ), 500

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
        data = get_brevo_client().get_contact_lists(limit=50)
        for item in data.get("lists", []):
            lists.append(
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "contacts": item.get("totalSubscribers", 0),
                    "date": _format_brevo_date(item.get("createdAt")),
                }
            )
    except Exception as e:
        app.logger.exception("Failed to fetch Brevo contact lists.")
        error = _brevo_error_message(
            e, "Could not load email lists from Brevo. Check the BREVO_API_KEY and try again."
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
            e, "Could not load this list from Brevo. Check the BREVO_API_KEY and try again."
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
    writer.writerow(["Contact Name", "Email", "Creation Date", "Last Changed", "Blacklisted"])
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
            opens, clicks, unsubs, date = stats["opens"], stats["clicks"], stats["unsubs"], stats["date"]
        except Exception:
            app.logger.exception("Failed to fetch Brevo stats for campaign %s", campaign.id)

        rows.append({
            "id": campaign.id,
            "name": campaign.subject or "Untitled campaign",
            "list": campaign.contact_list.name if campaign.contact_list else "—",
            "opens": opens,
            "clicks": clicks,
            "unsub": unsubs,
            "date": date,
            "scheduled": campaign.status == EmailCampaign.STATUS_SCHEDULED,
            "status": campaign.status.capitalize(),
            "quarterly": "Running" if campaign.recurrence_rule == EmailCampaign.RECURRENCE_QUARTERLY else "Activate",
        })

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
        flash("Quarterly run deactivated.", "success")
    else:
        campaign.recurrence_rule = EmailCampaign.RECURRENCE_QUARTERLY
        campaign.next_run_at = utc_now() + timedelta(days=90)
        flash("Quarterly run activated.", "success")

    db.session.commit()
    return redirect(url_for("campaign_manager"))


@app.post("/campaign-manager/<campaign_id>/delete")
@login_required
def campaign_manager_delete(campaign_id):
    campaign = db.session.get(EmailCampaign, campaign_id)
    if not campaign:
        abort(404)

    if campaign.brevo_campaign_id:
        try:
            get_brevo_client().delete_email_campaign(campaign.brevo_campaign_id)
        except BrevoAPIError:
            app.logger.exception("Failed to delete Brevo campaign %s", campaign.brevo_campaign_id)
            flash("Could not delete this campaign on Brevo.", "error")
            return redirect(url_for("campaign_manager"))

    db.session.delete(campaign)
    db.session.commit()
    flash("Campaign deleted.", "success")
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

            campaign.logo_url = url_for("static", filename=f"uploads/logos/{logo_filename}")

        db.session.commit()

        return jsonify({"success": True, "next_url": url_for("wizard_template", mode=mode)})

    return render_template("wizard_setup.html", mode=mode)


@app.route("/campaign-manager/wizard/<mode>/template", methods=["GET"])
@login_required
def wizard_template(mode):
    if mode not in EmailCampaign.MODES:
        abort(404)

    campaign = get_current_campaign()
    if not campaign or not campaign.extracted_text:
        return redirect(url_for("campaign_wizard", mode=mode))

    if mode == EmailCampaign.MODE_AI and not campaign.email_content and not campaign.is_generating:
        _start_generation(campaign)
    elif mode == EmailCampaign.MODE_MANUAL and campaign.email_content is None:
        campaign.email_content = f"<p>{campaign.extracted_text}</p>" + _feedback_footer_html()
        db.session.commit()

    contact_lists = sync_contact_lists()

    return render_template(
        "wizard_template.html",
        mode=mode,
        campaign=campaign,
        is_generating=campaign.is_generating,
        sender_emails=["info@larhdellaw.com"],
        sender_name=campaign.sender_name or "Larhdel Law",
        email_subject=campaign.subject or "US Immigration Update for 2026 - Larhdel Law",
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
    return jsonify({
        "is_generating": campaign.is_generating,
        "email_content": campaign.email_content,
    })


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

    campaign.sender_email = payload.get("sender_email") or campaign.sender_email
    campaign.sender_name = payload.get("sender_name") or campaign.sender_name
    campaign.subject = payload.get("email_subject") or campaign.subject

    contact_list_id = payload.get("email_list")
    if contact_list_id:
        campaign.contact_list_id = contact_list_id

    contact_list = db.session.get(ContactList, campaign.contact_list_id) if campaign.contact_list_id else None
    list_ids = [contact_list.brevo_list_id] if contact_list else None

    campaign_kwargs = dict(
        name=f"{campaign.subject or 'Untitled campaign'} ({mode})",
        subject=campaign.subject,
        sender_name=campaign.sender_name,
        sender_email=campaign.sender_email,
        html_content=campaign.email_content,
        list_ids=list_ids,
    )

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

    try:
        get_brevo_client().send_test_email(campaign.brevo_campaign_id, emails)
    except BrevoAPIError as e:
        return jsonify({"error": str(e)}), 502

    return jsonify({"success": True})


@app.route("/test-emails")
@login_required
def test_emails_page():
    contact_lists = sync_contact_lists()
    selected_list = next(
        (cl for cl in contact_lists if "test" in cl.name.strip().lower()),
        (contact_lists[0] if contact_lists else None),
    )

    brevo_contacts = []
    error = None
    if selected_list:
        try:
            existing_emails = {te.email for te in TestEmail.query.all()}
            data = get_brevo_client().get_contacts_from_list(selected_list.brevo_list_id, limit=500)
            for brevo_contact in data.get("contacts", []):
                email = brevo_contact.get("email")
                if not email:
                    continue
                brevo_contacts.append({
                    "name": _contact_display_name(brevo_contact),
                    "email": email,
                    "already_added": TestEmail.normalize_email(email) in existing_emails,
                })
        except Exception as e:
            app.logger.exception("Failed to fetch contacts for test-email picker.")
            error = _brevo_error_message(
                e, "Could not load contacts from Brevo. Check the BREVO_API_KEY and try again."
            )
    else:
        error = "No Brevo contact lists found."

    return render_template(
        "test_emails.html",
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

    flash(f"Added {added} test email(s)." if added else "No new test emails to add.", "success")
    return redirect(url_for("test_emails_page"))


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
        sender_emails=["info@larhdellaw.com"],
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
    brevo = get_brevo_client()

    try:
        if action == "run_now":
            brevo.update_email_campaign(campaign.brevo_campaign_id, html_content=campaign.email_content)
            brevo.send_campaign_now(campaign.brevo_campaign_id)
            campaign.status = EmailCampaign.STATUS_SENT
            db.session.add(CampaignRun(
                campaign_id=campaign.id,
                brevo_campaign_id=campaign.brevo_campaign_id,
                run_at=utc_now(),
                status=CampaignRun.STATUS_SENT,
            ))
        elif action == "schedule":
            scheduled_at = payload.get("scheduled_at")
            if not scheduled_at:
                return jsonify({"error": "A schedule date/time is required."}), 400
            # The <input type="datetime-local"> value is naive (no tz) — treat it as
            # the server's local time, since Brevo requires a tz-aware ISO timestamp.
            naive_dt = datetime.fromisoformat(scheduled_at)
            local_tz = datetime.now().astimezone().tzinfo
            aware_dt = naive_dt.replace(tzinfo=local_tz)
            brevo.update_email_campaign(campaign.brevo_campaign_id, scheduled_at=aware_dt.isoformat())
            campaign.scheduled_at = naive_dt
            campaign.status = EmailCampaign.STATUS_SCHEDULED
            db.session.add(CampaignRun(
                campaign_id=campaign.id,
                brevo_campaign_id=campaign.brevo_campaign_id,
                run_at=campaign.scheduled_at,
                status=CampaignRun.STATUS_SCHEDULED,
            ))
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
            data = get_brevo_client().get_contacts_from_list(campaign.contact_list.brevo_list_id, limit=500)
            for brevo_contact in data.get("contacts", []):
                email = brevo_contact.get("email", "")
                normalized_email = Contact.normalize_email(email)
                local_contact = Contact.query.filter_by(email=normalized_email).one_or_none()
                if local_contact is None:
                    local_contact = Contact(
                        email=normalized_email,
                        is_blacklisted=bool(brevo_contact.get("emailBlacklisted")),
                    )
                    db.session.add(local_contact)
                contacts.append({
                    "name": _contact_display_name(brevo_contact),
                    "email": email,
                    "created": _format_brevo_date(brevo_contact.get("createdAt")),
                    "changed": _format_brevo_date(brevo_contact.get("modifiedAt")),
                    "classification": local_contact.classification,
                    "blacklisted": local_contact.is_blacklisted,
                })
            db.session.commit()
        except Exception as e:
            app.logger.exception("Failed to fetch recipients for campaign %s", campaign_id)
            error = _brevo_error_message(
                e, "Could not load recipients from Brevo. Check the BREVO_API_KEY and try again."
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
        app.logger.exception("Failed to push classification to Brevo for %s", contact.email)

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
        sender_emails=[campaign.sender_email] if campaign.sender_email else ["info@larhdellaw.com"],
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


@app.route("/reports")
@login_required
def reports():
    campaigns = []
    error = None
    try:
        data = get_brevo_client().get_email_campaigns(limit=50)
        campaigns = [_campaign_stats(c) for c in data.get("campaigns", [])]
    except Exception as e:
        app.logger.exception("Failed to fetch Brevo campaigns.")
        error = _brevo_error_message(
            e, "Could not load reports from Brevo. Check the BREVO_API_KEY and try again."
        )
    return render_template("reports.html", campaigns=campaigns, error=error)


@app.post("/reports/<int:campaign_id>/delete")
@login_required
def report_delete(campaign_id):
    try:
        get_brevo_client().delete_email_campaign(campaign_id)
        flash("Campaign deleted.", "success")
    except Exception:
        app.logger.exception("Failed to delete Brevo campaign.")
        flash("Could not delete this campaign on Brevo.", "error")
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
    writer.writerow(["Campaign Name", "Opens", "Clicks", "Unsubscribes", "Creation Date"])
    writer.writerow([row["name"], row["opens"], row["clicks"], row["unsubs"], row["date"]])

    filename = secure_filename(row["name"] or f"campaign-{campaign_id}") or f"campaign-{campaign_id}"
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

        flash(f"Account created. A password setup link was emailed to {email}.", "success")
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
        flash("This password setup link is invalid or has expired. Please contact an administrator.", "error")
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


@app.route("/campaigns/ai-wizard/upload", methods=["GET", "POST"])
@login_required
def ai_wizard_upload():
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

        draft_id = _get_or_create_draft_id()
        update_fields = {
            "content_file_bytes": content_bytes,
            "content_file_name": secure_filename(content_file.filename),
            "extracted_text": extracted_text,
            "cta_links": cta_links,
        }

        if logo_file and logo_file.filename != "":
            if not _allowed_file(logo_file.filename, ALLOWED_LOGO_EXTENSIONS):
                return jsonify({"error": "Unsupported logo file type."}), 400

            logo_bytes = logo_file.read()
            if len(logo_bytes) > MAX_UPLOAD_SIZE:
                return jsonify({"error": "Logo file exceeds 5MB limit."}), 400

            os.makedirs(LOGO_UPLOAD_DIR, exist_ok=True)
            ext = logo_file.filename.rsplit(".", 1)[1].lower()
            logo_filename = f"{draft_id}.{ext}"
            with open(os.path.join(LOGO_UPLOAD_DIR, logo_filename), "wb") as f:
                f.write(logo_bytes)

            update_fields["logo_url"] = url_for(
                "static", filename=f"uploads/logos/{logo_filename}"
            )

        update_draft(draft_id, **update_fields)
        _start_generation(draft_id)

        return jsonify({"success": True, "next_url": url_for("ai_wizard_template")})

    return render_template("email_campaign/ai_wizard_upload.html")


@app.route("/campaigns/ai-wizard/template")
@login_required
def ai_wizard_template():
    draft_id = session.get("draft_id")
    draft = get_draft(draft_id)

    if not draft or not draft.get("extracted_text"):
        return redirect(url_for("ai_wizard_upload"))

    return render_template(
        "email_campaign/ai_wizard_template.html",
        is_generating=draft.get("is_generating", False),
        sender_emails=["info@larhdellaw.com"],
        sender_name=draft.get("sender_name") or "Larhdel Law",
        email_subject=draft.get("email_subject") or "US Immigration Update for 2026 - Larhdel Law",
        email_lists=[{"id": "1", "name": "Email List 1"}],
        email_content=draft.get("email_content"),
        logo_url=draft.get("logo_url"),
    )

@app.route("/campaigns/ai-wizard/status")
@login_required
def ai_wizard_status():
    draft_id = session.get("draft_id")
    draft = get_draft(draft_id)
    if not draft:
        return jsonify({"error": "No draft found"}), 404

    return jsonify({
        "is_generating": draft.get("is_generating", False),
        "email_content": draft.get("email_content"),
    })
    
@app.route("/campaigns/ai-wizard/save-content", methods=["POST"])
@login_required
def ai_wizard_save_content():
    draft_id = session.get("draft_id")
    draft = get_draft(draft_id)
    if not draft:
        return jsonify({"error": "No draft found"}), 404

    payload = request.get_json(silent=True) or {}
    edited_html = payload.get("email_content", "")
    update_draft(draft_id, email_content=edited_html)
    return jsonify({"success": True})

@app.route("/campaigns/ai-wizard/replace-content", methods=["POST"])
@login_required
def ai_wizard_replace_content():
    draft_id = session.get("draft_id")
    draft = get_draft(draft_id)
    if not draft:
        return jsonify({"error": "No draft found"}), 404

    content_file = request.files.get("content_file")
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

    update_draft(
        draft_id,
        content_file_bytes=content_bytes,
        content_file_name=secure_filename(content_file.filename),
        extracted_text=extracted_text,
    )
    _start_generation(draft_id)

    return jsonify({"success": True})

@app.route("/campaigns/ai-wizard/send")
@login_required
def ai_wizard_test_send():
    # TODO: replace with the real campaign draft pulled from session/DB
    # (the same draft the user set up in the previous "Setup Email Template" step)
    campaign = get_current_campaign_draft()
    # TODO: replace with the user's actual saved test-email lists
    test_email_lists = get_test_email_lists()
    selected_list_id = request.args.get("test_email_list") or (
        test_email_lists[0]["id"] if test_email_lists else None
    )
    # TODO: replace with the emails belonging to the selected test list
    test_emails = get_test_emails(selected_list_id)

    return render_template(
        "email_campaign/ai_wizard_send.html",
        email_content=campaign.get("email_content"),
        logo_url=campaign.get("logo_url"),
        test_email_lists=test_email_lists,
        selected_test_email_list_id=selected_list_id,
        test_emails=test_emails,
    )

@app.route("/campaigns/ai-wizard/save-template-fields", methods=["POST"])
@login_required
def ai_wizard_save_template_fields():
    draft_id = session.get("draft_id")
    draft = get_draft(draft_id)
    if not draft:
        return jsonify({"error": "No draft found"}), 404

    payload = request.get_json(silent=True) or {}
    update_draft(
        draft_id,
        sender_email=payload.get("sender_email"),
        sender_name=payload.get("sender_name"),
        email_subject=payload.get("email_subject"),
        email_list=payload.get("email_list"),
    )
    return jsonify({"success": True})

@app.route("/campaigns/ai-wizard/test-send/send", methods=["POST"])
@login_required
def ai_wizard_test_send_action():
    payload = request.get_json(silent=True) or {}
    list_id = payload.get("test_email_list")

    # TODO: replace with real send logic (e.g. dispatch through your ESP/mailer)
    sent = send_test_emails(list_id)

    return jsonify({"success": sent})


@app.route("/campaigns/ai-wizard/schedule")
@login_required
def ai_wizard_schedule():
    draft_id = session.get("draft_id")
    draft = get_draft(draft_id)

    if not draft or not draft.get("email_content"):
        return redirect(url_for("ai_wizard_upload"))

    return render_template(
        "email_campaign/ai_wizard_schedule.html",
        sender_emails=[draft.get("sender_email") or "info@larhdellaw.com"],
        sender_name=draft.get("sender_name") or "Larhdel Law",
        email_subject=draft.get("email_subject") or "US Immigration Update for 2026 - Larhdel Law",
        email_lists=[{"id": "1", "name": "Email List 1"}],
        email_content=draft.get("email_content"),
        logo_url=draft.get("logo_url"),
        status=draft.get("status", "draft"),
        scheduled_at=draft.get("scheduled_at"),
    )

if __name__ == "__main__":
    app.run(debug=True)
