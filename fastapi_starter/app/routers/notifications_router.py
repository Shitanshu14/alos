"""
Week 8 — Notification endpoints.

Lists the logged-in user's in-app notifications and lets them mark one
(or all) as read. Notifications themselves are created by
services/notification_service.py from other parts of the pipeline
(e.g. chapters_router.py, once a chapter finishes processing).
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from .auth_router import get_current_user

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("/", response_model=list[schemas.NotificationOut], summary="List my notifications")
def list_notifications(
    unread_only: bool = False,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(models.Notification).filter(models.Notification.user_id == current_user.id)
    if unread_only:
        q = q.filter(models.Notification.is_read == False)  # noqa: E712
    return q.order_by(models.Notification.created_at.desc()).all()


@router.patch(
    "/{notification_id}/read",
    response_model=schemas.NotificationOut,
    summary="Mark a notification as read",
)
def mark_notification_read(
    notification_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    notif = (
        db.query(models.Notification)
        .filter(models.Notification.id == notification_id, models.Notification.user_id == current_user.id)
        .first()
    )
    if not notif:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    notif.is_read = True
    db.commit()
    db.refresh(notif)
    return notif


@router.patch(
    "/read-all",
    response_model=schemas.MessageResponse,
    summary="Mark all my notifications as read",
)
def mark_all_read(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    updated = (
        db.query(models.Notification)
        .filter(models.Notification.user_id == current_user.id, models.Notification.is_read == False)  # noqa: E712
        .update({"is_read": True})
    )
    db.commit()
    return {"message": f"Marked {updated} notification(s) as read"}
