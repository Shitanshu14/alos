"""
Email bhejne ka helper.

Agar SMTP_HOST configure nahi hai (.env me), to email console/log me
print ho jaayegi — taaki development me bina real SMTP setup ke bhi
test kar sako. Production me .env me SMTP_HOST, SMTP_USER, SMTP_PASSWORD
waghera daal dena (Gmail, SendGrid, AWS SES, Mailgun — jo bhi use karo).
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


def send_email(to_email: str, subject: str, body: str) -> None:
    if not settings.SMTP_HOST:
        # Dev mode: SMTP configured nahi hai, isliye console me print kar rahe hain
        logger.info(
            "[DEV MODE - EMAIL NOT SENT] To: %s | Subject: %s\n%s",
            to_email, subject, body,
        )
        return

    try:
        _send_via_smtp(to_email, subject, body)
        logger.info("Email sent to %s | Subject: %s", to_email, subject)
    except Exception:
        # Email fail hone se signup/reset flow break nahi hona chahiye,
        # lekin error zaroor log hona chahiye taaki pata chale.
        logger.exception("Failed to send email to %s", to_email)


def send_verification_email(to_email: str, token: str) -> None:
    link = f"{settings.FRONTEND_URL}/verify-email?token={token}"
    body = (
        f"Namaste,\n\nApna email verify karne ke liye ye link kholiye:\n{link}\n\n"
        f"Ye link {settings.VERIFY_TOKEN_EXPIRE_HOURS} ghante ke liye valid hai.\n\n"
        f"Agar aapne signup nahi kiya, is email ko ignore kar dijiye."
    )
    send_email(to_email, "Apna email verify karein", body)


def send_password_reset_email(to_email: str, token: str) -> None:
    link = f"{settings.FRONTEND_URL}/reset-password?token={token}"
    body = (
        f"Namaste,\n\nPassword reset karne ke liye ye link kholiye:\n{link}\n\n"
        f"Ye link {settings.RESET_TOKEN_EXPIRE_MINUTES} minute ke liye valid hai.\n\n"
        f"Agar aapne ye request nahi ki, is email ko ignore kar dijiye — "
        f"aapka password change nahi hoga."
    )
    send_email(to_email, "Password reset request", body)
