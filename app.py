from flask import Flask, abort, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

from config import Config
from extensions import db, login_manager, migrate
from logging_config import setup_logging
from models import User

setup_logging()

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)
migrate.init_app(app, db)
login_manager.init_app(app)


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
    return redirect(url_for("login"))


@app.route("/dashboard")
def index():
    return render_template("index.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
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
    if request.method == "POST":
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        return redirect(url_for("login"))
    return render_template("forgot_password.html")


@app.route("/logout")
def logout():
    return redirect(url_for("login"))


@app.route("/email-lists")
def email_lists():
    return render_template("email_lists.html")


@app.route("/email-lists/<list_id>")
def email_list_detail(list_id):
    return render_template("email_list_detail.html", list_id=list_id)


@app.route("/campaign-manager")
def campaign_manager():
    return render_template("campaign_manager.html")


@app.route("/campaign-manager/wizard/<mode>")
def campaign_wizard(mode):
    if mode not in ("ai", "manual"):
        abort(404)
    return render_template("wizard_setup.html", mode=mode)


@app.route("/campaign-manager/wizard/<mode>/template")
def wizard_template(mode):
    if mode not in ("ai", "manual"):
        abort(404)
    return render_template("wizard_template.html", mode=mode)


@app.route("/campaign-manager/wizard/<mode>/test-send")
def wizard_test_send(mode):
    if mode not in ("ai", "manual"):
        abort(404)
    return render_template("wizard_test_send.html", mode=mode)


@app.route("/campaign-manager/wizard/<mode>/schedule")
def wizard_schedule(mode):
    if mode not in ("ai", "manual"):
        abort(404)
    return render_template("wizard_schedule.html", mode=mode)


@app.route("/campaign-manager/<campaign_id>")
def campaign_preview(campaign_id):
    return render_template("campaign_preview.html", campaign_id=campaign_id)


@app.route("/reports")
def reports():
    return render_template("reports.html")


@app.route("/user-accounts")
def user_accounts():
    return render_template("user_accounts.html")


@app.route("/user-accounts/edit")
def user_form():
    return render_template("user_form.html")


@app.route("/campaigns/ai-wizard/upload")
def ai_wizard_upload():
    return render_template("email_campaign/ai_wizard_upload.html")


@app.route("/campaigns/ai-wizard/template")
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
def ai_wizard_test_send_action():
    payload = request.get_json(silent=True) or {}
    list_id = payload.get("test_email_list")

    # TODO: replace with real send logic (e.g. dispatch through your ESP/mailer)
    sent = send_test_emails(list_id)

    return jsonify({"success": sent})


@app.route("/campaigns/ai-wizard/schedule")
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
