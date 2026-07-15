import smtplib
from email.message import EmailMessage

from flask import current_app


def send_password_reset_email(to_email, code):
    host = current_app.config.get("SMTP_HOST")
    port = current_app.config.get("SMTP_PORT")
    username = current_app.config.get("SMTP_USERNAME")
    password = current_app.config.get("SMTP_PASSWORD")
    use_tls = current_app.config.get("SMTP_USE_TLS")
    sender = current_app.config.get("SMTP_DEFAULT_SENDER") or username
    expiry_minutes = current_app.config.get("PASSWORD_RESET_CODE_MINUTES")

    if not all((host, port, username, password, sender)):
        raise RuntimeError("SMTP is not fully configured.")

    message = EmailMessage()
    message["Subject"] = "Your password reset code"
    message["From"] = sender
    message["To"] = to_email
    message.set_content(
        "\n".join(
            [
                "Use this code to reset your AI Email Agent password:",
                "",
                code,
                "",
                f"This code expires in {expiry_minutes} minutes. If you did not request it, you can ignore this email.",
            ]
        )
    )

    with smtplib.SMTP(host, port, timeout=15) as server:
        if use_tls:
            server.starttls()
        server.login(username, password)
        server.send_message(message)
