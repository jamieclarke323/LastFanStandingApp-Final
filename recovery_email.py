"""TLS-only SMTP delivery. Secrets are supplied through environment configuration."""

import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import urlsplit


def recovery_configured(config):
    public_url = urlsplit(config.get("PUBLIC_BASE_URL", ""))
    return bool(
        config.get("SMTP_HOST")
        and config.get("SMTP_USERNAME")
        and config.get("SMTP_PASSWORD")
        and config.get("SMTP_FROM")
        and public_url.scheme == "https"
        and public_url.netloc
        and not public_url.username
        and not public_url.password
        and not public_url.query
        and not public_url.fragment
        and len(config.get("SECRET_KEY", "")) >= 32
        and config.get("SECRET_KEY") != "dev-secret-key"
    )


def send_password_reset_email(config, recipient, reset_url):
    message = EmailMessage()
    message["Subject"] = "Reset your Last Fan Standing password"
    message["From"] = config["SMTP_FROM"]
    message["To"] = recipient
    message.set_content(
        "We received a request to reset your Last Fan Standing password.\n\n"
        f"Choose a new password here:\n{reset_url}\n\n"
        "This link expires in 30 minutes and can be used only once.\n"
        "If you did not request this, ignore this email. Your password has not changed.\n"
        "Do not forward this email or share the link.\n"
    )
    implicit_tls = config.get("SMTP_SSL", False)
    smtp_class = smtplib.SMTP_SSL if implicit_tls else smtplib.SMTP
    kwargs = {"timeout": 15}
    context = ssl.create_default_context()
    if implicit_tls:
        kwargs["context"] = context
    with smtp_class(config["SMTP_HOST"], config["SMTP_PORT"], **kwargs) as smtp:
        if not implicit_tls:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
        smtp.login(config["SMTP_USERNAME"], config["SMTP_PASSWORD"])
        if smtp.send_message(message):
            raise smtplib.SMTPException("Recipient rejected")