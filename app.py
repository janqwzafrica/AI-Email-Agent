from flask import Flask, render_template

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
