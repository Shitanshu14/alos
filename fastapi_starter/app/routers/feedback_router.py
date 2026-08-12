"""
Week 16 — Feedback intake endpoint/process.

Any logged-in user can submit feedback (bug/feature request/general).
Admins review it via GET /feedback and can update its status as they
work through the backlog.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from typing import Optional

from .. import models, schemas
from ..database import get_db
from .auth_router import get_current_user, require_admin

router = APIRouter(prefix="/feedback", tags=["Feedback"])


@router.post("/", response_model=schemas.FeedbackOut, status_code=status.HTTP_201_CREATED, summary="Submit feedback")
def submit_feedback(
    payload: schemas.FeedbackCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    feedback = models.Feedback(
        user_id=current_user.id,
        category=payload.category,
        message=payload.message,
        rating=payload.rating,
    )
    db.add(feedback)
    db.commit()
    db.refresh(feedback)
    return feedback


@router.get("/", response_model=list[schemas.FeedbackOut], summary="List feedback (admin only)")
def list_feedback(
    category: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = db.query(models.Feedback)
    if category:
        q = q.filter(models.Feedback.category == category)
    if status_filter:
        q = q.filter(models.Feedback.status == status_filter)
    return q.order_by(models.Feedback.created_at.desc()).all()


@router.patch("/{feedback_id}/status", response_model=schemas.FeedbackOut, summary="Update a feedback item's status (admin only)")
def update_feedback_status(
    feedback_id: int,
    new_status: str = Query(..., pattern="^(open|reviewed|resolved)$"),
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.query(models.Feedback).filter(models.Feedback.id == feedback_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feedback not found")
    item.status = new_status
    db.commit()
    db.refresh(item)
    return item
