from datetime import timedelta
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
from models import PasswordResetCode, Role, User, utc_now
from services.email_service import (
    send_password_reset_email,
    send_password_setup_email,
)
from utils.security import BCRYPT_MAX_BYTES
from services.content_draft_store import create_draft, get_draft, update_draft
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

def _get_or_create_draft_id():
    draft_id = session.get("draft_id")
    if not draft_id or get_draft(draft_id) is None:
        draft_id = create_draft()
        session["draft_id"] = draft_id
    return draft_id

def get_current_campaign_draft():
    draft_id = session.get("draft_id")
    draft = get_draft(draft_id)
    if not draft:
        return {"email_content": None, "logo_url": None}
    return draft

def _run_generation(app_ref, draft_id):
    with app_ref.app_context():
        draft = get_draft(draft_id)
        if not draft:
            return
        try:
            content = generate_email_content(
                extracted_text=draft["extracted_text"],
                cta_links=draft.get("cta_links", ""),
                sender_name=draft.get("sender_name") or "Larhdel Law",
            )
            update_draft(draft_id, email_content=content, is_generating=False)
        except AIGenerationError as e:
            update_draft(
                draft_id,
                email_content=f"<p>We couldn't generate content automatically: {e}</p>",
                is_generating=False,
            )
        except Exception:
            # Catch-all so a stuck pill can never happen again — any unexpected
            # error still resolves the generating state instead of hanging forever.
            current_app.logger.exception("Unexpected error during AI generation")
            update_draft(
                draft_id,
                email_content="<p>Something went wrong generating this content. Please try again.</p>",
                is_generating=False,
            )

def _start_generation(draft_id):
    update_draft(draft_id, is_generating=True, email_content=None)
    thread = threading.Thread(
        target=_run_generation, args=(app, draft_id), daemon=True
    )
    thread.start()
    

def get_test_email_lists():
    """Return the user's saved test-email lists as [{'id': ..., 'name': ...}, ...]."""
    return []


def get_test_emails(list_id):
    """Return the list of email addresses belonging to the given test list id."""
    return []


def send_test_emails(list_id):
    """Send the current draft to every address in the given test list."""
    return True


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


@app.route("/email-lists")
@login_required
def email_lists():
    return render_template("email_lists.html")


@app.route("/email-lists/<list_id>")
@login_required
def email_list_detail(list_id):
    return render_template("email_list_detail.html", list_id=list_id)


@app.route("/campaign-manager")
@login_required
def campaign_manager():
    return render_template("campaign_manager.html")


@app.route("/campaign-manager/wizard/<mode>")
@login_required
def campaign_wizard(mode):
    if mode not in ("ai", "manual"):
        abort(404)
    return render_template("wizard_setup.html", mode=mode)


@app.route("/campaign-manager/wizard/<mode>/template")
@login_required
def wizard_template(mode):
    if mode not in ("ai", "manual"):
        abort(404)
    return render_template("wizard_template.html", mode=mode)


@app.route("/campaign-manager/wizard/<mode>/test-send")
@login_required
def wizard_test_send(mode):
    if mode not in ("ai", "manual"):
        abort(404)
    return render_template("wizard_test_send.html", mode=mode)


@app.route("/campaign-manager/wizard/<mode>/schedule")
@login_required
def wizard_schedule(mode):
    if mode not in ("ai", "manual"):
        abort(404)
    return render_template("wizard_schedule.html", mode=mode)


@app.route("/campaign-manager/<campaign_id>")
@login_required
def campaign_recipients(campaign_id):
    return render_template("campaign_recipients.html", campaign_id=campaign_id)


@app.route("/campaign-manager/<campaign_id>/preview")
@login_required
def campaign_preview(campaign_id):
    return render_template("campaign_preview.html", campaign_id=campaign_id)


@app.route("/reports")
@login_required
def reports():
    return render_template("reports.html")


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
