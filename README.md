# AI Email Agent

A Flask web app. Frontend is built with vanilla HTML/CSS/JS (no build step); backend packages are placeholders for a later phase.

## Setup

```
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

pip install -r requirements.txt
copy .env.example .env        # Windows (cp on macOS/Linux)
```

Create MySQL databases for the app and tests, then update `.env`:

```
DB_HOST=localhost
DB_NAME=managewhat
DB_USER=root
DB_PASSWORD=
TEST_DATABASE_URL=mysql+pymysql://user:password@127.0.0.1:3306/ai_email_agent_test
```

The app builds its MySQL SQLAlchemy URL internally from the `DB_*` values.
`TEST_DATABASE_URL` is only for local/CI tests because tests reset their schema.

Apply migrations:

```
flask db upgrade
```

## Run

```
python app.py
```

Then open http://127.0.0.1:5000/
