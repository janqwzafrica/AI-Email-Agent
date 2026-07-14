from flask import Flask, abort, render_template

from config import Config
from logging_config import setup_logging

setup_logging()

app = Flask(__name__)
app.config.from_object(Config)


@app.route("/")
def index():
    return render_template("index.html")


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


if __name__ == "__main__":
    app.run(debug=True)
