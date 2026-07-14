from flask import Flask, render_template

from config import Config
from logging_config import setup_logging

setup_logging()

app = Flask(__name__)
app.config.from_object(Config)


@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(debug=True)
