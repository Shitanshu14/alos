"""
Week 2 — Role-based dashboard API.

Returns different data depending on the logged-in user's role
(teacher / parent / student / school / admin).

Only "teacher"/"school" get real data right now, because that's the only
piece of the pipeline built so far (chapter uploads, Week 3). Parent and
student views are wired up but honestly say "not available yet" instead
of faking data - they'll return real content once:
  - parent  -> parent-child linking lands (Week 10)
  - student -> progress tracking + content delivery lands (Week 8-9)
"""
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from .. import models, schemas
from ..database import get_db
from .auth_router import get_current_user

logger = logging.getLogger("app.dashboard")

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get(
    "/",
    response_model=schemas.DashboardOut,
    summary="Get dashboard data for the logged-in user (varies by role)",
)
def get_dashboard(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    role = current_user.role

    if role in ("teacher", "school"):
        return _teacher_or_school_dashboard(current_user, db)
    elif role == "parent":
        return _parent_dashboard(current_user)
    elif role == "student":
        return _student_dashboard(current_user)
    else:
        # admin or any future role - nothing role-specific to show yet
        return schemas.DashboardOut(role=role, message="No dashboard view configured for this role yet.")


def _teacher_or_school_dashboard(user: models.User, db: Session) -> schemas.DashboardOut:
    chapters = (
        db.query(models.Chapter)
        .filter(models.Chapter.uploaded_by == user.id)
        .order_by(models.Chapter.created_at.desc())
        .all()
    )

    status_counts = (
        db.query(models.Chapter.status, func.count(models.Chapter.id))
        .filter(models.Chapter.uploaded_by == user.id)
        .group_by(models.Chapter.status)
        .all()
    )

    return schemas.DashboardOut(
        role=user.role,
        message=f"{len(chapters)} chapter(s) uploaded so far.",
        chapters_by_status={status: count for status, count in status_counts},
        recent_chapters=[schemas.ChapterOut.model_validate(c) for c in chapters[:10]],
    )


def _parent_dashboard(user: models.User) -> schemas.DashboardOut:
    return schemas.DashboardOut(
        role=user.role,
        message=(
            "Parent-child linking isn't built yet (planned for Week 10). "
            "Once available, this will show each child's learning progress."
        ),
    )


def _student_dashboard(user: models.User) -> schemas.DashboardOut:
    return schemas.DashboardOut(
        role=user.role,
        message=(
            "Assigned content and progress tracking aren't built yet "
            "(planned for Week 8-9). Check back once the content pipeline is live."
        ),
    )
