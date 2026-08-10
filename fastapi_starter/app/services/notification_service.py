"""
Week 8 — Notification service (email / push / in-app).

Creates an in-app Notification row (what a notification bell/list in the
frontend would read from) and, optionally, sends an email alongside it
using the existing SMTP helper (email_utils.py - same dev-mode-logs-
instead-of-sending behavior when SMTP isn't configured).

Push notifications aren't implemented here - there's no mobile app /
device-token infrastructure yet to send them through. The function
signature below leaves room for it (see the `send_push` param, which
currently just logs a note) so wiring in real push later doesn't
require changing every call site.
"""
import logging

from .. import models
from ..email_utils import send_email

logger = logging.getLogger("app.services.notification_service")


def notify(
    db,
    user: models.User,
    title: str,
    message: str,
    send_email_too: bool = False,
    send_push: bool = False,
) -> models.Notification:
    """Creates an in-app notification for `user`, and optionally emails them
    the same message. Returns the created Notification row."""
    notification = models.Notification(
        user_id=user.id,
        title=title,
        message=message,
    )
    db.add(notification)
    db.flush()

    if send_email_too:
        send_email(user.email, title, message)

    if send_push:
        # No push infrastructure (device tokens / FCM/APNs) yet - log it so
        # it's visible during development that this notification *would*
        # have gone out as a push once that's wired up.
        logger.info("[PUSH NOT IMPLEMENTED] Would push to user %s: %s", user.id, title)

    return notification
