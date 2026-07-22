import smtplib
from email.message import EmailMessage
from email.utils import formataddr

from flask import current_app


def _smtp_config():
    host = current_app.config.get("SMTP_HOST")
    port = current_app.config.get("SMTP_PORT")
    username = current_app.config.get("SMTP_USERNAME")
    password = current_app.config.get("SMTP_PASSWORD")
    use_tls = current_app.config.get("SMTP_USE_TLS")
    sender = current_app.config.get("SMTP_DEFAULT_SENDER") or username

    if not all((host, port, username, password, sender)):
        raise RuntimeError("SMTP is not fully configured.")
    return host, port, username, password, use_tls, sender


def _send_smtp_message(message):
    host, port, username, password, use_tls, _ = _smtp_config()
    with smtplib.SMTP(host, port, timeout=15) as server:
        if use_tls:
            server.starttls()
        server.login(username, password)
        server.send_message(message)


def _send_email(subject, to_email, body):
    *_, sender = _smtp_config()
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to_email
    message.set_content(body)
    _send_smtp_message(message)


def send_password_setup_email(to_email, setup_link):
    hours = current_app.config.get("PASSWORD_SETUP_LINK_HOURS")
    _send_email(
        "Set up your AI Email Agent account",
        to_email,
        "\n".join(
            [
                "An account has been created for you on AI Email Agent.",
                "",
                "Click the link below to set your password and activate your account:",
                "",
                setup_link,
                "",
                f"This link expires in {hours} hours. If you were not expecting this email, you can ignore it.",
            ]
        ),
    )


def send_password_reset_email(to_email, code):
    expiry_minutes = current_app.config.get("PASSWORD_RESET_CODE_MINUTES")
    _send_email(
        "Your password reset code",
        to_email,
        "\n".join(
            [
                "Use this code to reset your AI Email Agent password:",
                "",
                code,
                "",
                f"This code expires in {expiry_minutes} minutes. If you did not request it, you can ignore this email.",
            ]
        ),
    )


def send_campaign_email(to_email, subject, html_content, sender_name=None):
    """Send one personalized campaign email over the manual SMTP account.

    Uses the authenticated SMTP account's address as the actual From address
    (most SMTP relays reject/flag arbitrary From addresses), with
    sender_name as the display name if given — e.g. "Larhdel Law <acct@...>".
    """
    *_, sender_address = _smtp_config()
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = (
        formataddr((sender_name, sender_address)) if sender_name else sender_address
    )
    message["To"] = to_email
    message.set_content(html_content, subtype="html")
    _send_smtp_message(message)
