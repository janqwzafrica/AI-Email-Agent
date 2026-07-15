import os
import threading
from flask import Flask, current_app, redirect, render_template, request, url_for, jsonify, session
from werkzeug.utils import secure_filename
from config import Config
from logging_config import setup_logging
from services.content_draft_store import create_draft, get_draft, update_draft
from services.document_extractor import extract_text, ExtractionError
from services.ai_email_content import generate_email_content, AIGenerationError

setup_logging()

app = Flask(__name__)
app.config.from_object(Config)

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


@app.route("/")
def auth():
    return redirect(url_for("login"))


@app.route("/dashboard")
def index():
    return render_template("index.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
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


@app.route("/reports")
def reports():
    return render_template("reports.html")


@app.route("/user-accounts")
def user_accounts():
    return render_template("user_accounts.html")


@app.route("/user-accounts/edit")
def user_form():
    return render_template("user_form.html")


@app.route("/campaigns/ai-wizard/upload", methods=["GET", "POST"])
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