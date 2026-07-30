"""
Email sending helper.

If SMTP_HOST is not configured (in .env), the email is just logged to the
console instead of actually being sent — so you can develop without a real
SMTP setup. In production, set SMTP_HOST, SMTP_USER, SMTP_PASSWORD, etc. in
.env (Gmail, SendGrid, AWS SES, Mailgun — whichever you use).

send_email() returns True only if the email was actually sent via SMTP.
Callers use this to avoid telling the user "check your inbox" when no
email was actually sent.
"""
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from .config import settings

logger = logging.getLogger("app.email")


def _send_via_smtp(to_email: str, subject: str, body: str) -> None:
    msg = MIMEMultipart()
    msg["From"] = settings.SMTP_FROM_EMAIL
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
        if settings.SMTP_USE_TLS:
            server.starttls()
        if settings.SMTP_USER:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.sendmail(settings.SMTP_FROM_EMAIL, [to_email], msg.as_string())


def send_email(to_email: str, subject: str, body: str) -> bool:
    """Returns True if the email was actually sent via SMTP, False otherwise."""
    if not settings.SMTP_HOST:
        # Dev mode: no SMTP configured, so just log it instead of sending
        logger.info(
            "[DEV MODE - EMAIL NOT SENT] To: %s | Subject: %s\n%s",
            to_email, subject, body,
        )
        return False

    try:
        _send_via_smtp(to_email, subject, body)
        logger.info("Email sent to %s | Subject: %s", to_email, subject)
        return True
    except Exception:
        # An email failure shouldn't break the signup/reset flow,
        # but it must be logged so it's noticed.
        logger.exception("Failed to send email to %s", to_email)
        return False


def send_verification_email(to_email: str, token: str) -> bool:
    link = f"{settings.FRONTEND_URL}/verify-email?token={token}"
    body = (
        f"Hello,\n\nPlease open this link to verify your email:\n{link}\n\n"
        f"This link is valid for {settings.VERIFY_TOKEN_EXPIRE_HOURS} hours.\n\n"
        f"If you didn't sign up, please ignore this email."
    )
    return send_email(to_email, "Verify your email", body)


def send_password_reset_email(to_email: str, token: str) -> bool:
    link = f"{settings.FRONTEND_URL}/reset-password?token={token}"
    body = (
        f"Hello,\n\nPlease open this link to reset your password:\n{link}\n\n"
        f"This link is valid for {settings.RESET_TOKEN_EXPIRE_MINUTES} minutes.\n\n"
        f"If you didn't request this, please ignore this email — "
        f"your password will not be changed."
    )
    return send_email(to_email, "Password reset request", body)
