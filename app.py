from datetime import timedelta
from urllib.parse import urlsplit

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import IntegrityError

from config import Config
from extensions import db, login_manager, migrate
from logging_config import setup_logging
from models import PasswordResetCode, User, utc_now
from services.email_service import send_password_reset_email

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


def get_current_campaign_draft():
    """Return the in-progress campaign draft (content + logo) from session/DB."""
    return {
        "email_content": None,
        "logo_url": None,
    }


def get_test_email_lists():
    """Return the user's saved test-email lists as [{'id': ..., 'name': ...}, ...]."""
    return []


def get_test_emails(list_id):
    """Return the list of email addresses belonging to the given test list id."""
    return []


def send_test_emails(list_id):
    """Send the current draft to every address in the given test list."""
    return True


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

        if password != password_confirm:
            flash("Passwords do not match.", "error")
            return render_template("signup.html", email=email), 400

        if User.query.filter_by(email=email).first():
            flash("An account with this email already exists.", "error")
            return render_template("signup.html", email=email), 409

        user = User(email=email, access_level=User.ROLE_ADMIN)
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
def user_accounts():
    return render_template("user_accounts.html")


@app.route("/user-accounts/edit")
@login_required
def user_form():
    return render_template("user_form.html")


@app.route("/campaigns/ai-wizard/upload")
@login_required
def ai_wizard_upload():
    return render_template("email_campaign/ai_wizard_upload.html")


@app.route("/campaigns/ai-wizard/template")
@login_required
def ai_wizard_template():
    return render_template(
        "email_campaign/ai_wizard_template.html",
        is_generating=True,
        sender_emails=["info@larhdellaw.com"],
        sender_name="Larhdel Law",
        email_subject="US Immigration Update for 2026 - Larhdel Law",
        email_lists=[{"id": "1", "name": "Email List 1"}],
        email_content="<p>Dear {{ contact.FIRSTNAME }},</p>",
        logo_url=None,
    )


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
    # Pulling the current campaign draft just like in the test-send step
    campaign = get_current_campaign_draft()
    
    return render_template(
        "email_campaign/ai_wizard_schedule.html",
        sender_emails=["info@larhdellaw.com"],
        sender_name="Larhdel Law",
        email_subject="US Immigration Update for 2026 - Larhdel Law",
        email_lists=[{"id": "1", "name": "Email List 1"}],
        email_content=campaign.get("email_content") or "<p>Dear {{ contact.FIRSTNAME }},</p>",
        logo_url=campaign.get("logo_url")
    )


if __name__ == "__main__":
    app.run(debug=True)
