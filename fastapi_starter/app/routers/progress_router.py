"""
Week 8 — Content access/streaming endpoint + resume-progress tracking.
Week 9 — Progress tracking: completed-chapters, continue-learning, history.

All progress here is tracked against the logged-in User (owner_type="user").
Week 10 adds Child profiles, which get their own progress rows
(owner_type="child") written via the parent-facing endpoints in
children_router.py - the read-side logic below works the same for both,
it just filters by owner_type/owner_id.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from .auth_router import get_current_user

logger = logging.getLogger("app.progress")

router = APIRouter(prefix="/progress", tags=["Progress"])


def _content_item_out(item: models.ContentItem) -> schemas.ContentItemOut:
    return schemas.ContentItemOut(
        id=item.id,
        title=item.title,
        content_type=item.content_type,
        subject=item.subject,
        class_name=item.class_name,
        description=item.description,
        body=item.body,
        media_url=item.media_url,
        is_seed_placeholder=item.is_seed_placeholder,
        is_generated=item.is_generated,
        tags=[t.name for t in item.tags],
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def get_or_create_progress(
    db: Session, owner_type: str, owner_id: int, content_item: models.ContentItem,
    chapter_id: Optional[int] = None,
) -> models.ContentProgress:
    query = db.query(models.ContentProgress).filter(
        models.ContentProgress.owner_type == owner_type,
        models.ContentProgress.owner_id == owner_id,
        models.ContentProgress.content_item_id == content_item.id,
    )
    if chapter_id is not None:
        # Caller told us which chapter context this is - a shared library
        # item can be matched to several chapters, so scope strictly to
        # this one (lets the same item have separate progress per chapter).
        query = query.filter(models.ContentProgress.chapter_id == chapter_id)
    # When no chapter_id is given, deliberately DON'T filter on it - we
    # want to find whatever progress row already exists for this
    # (owner, item) pair, regardless of which chapter_id it happened to
    # get auto-resolved to on creation. Filtering by chapter_id=None here
    # would miss a previously-created row that resolved to a real chapter
    # id, causing progress to appear to "reset" on every repeat access.
    progress = query.first()

    if not progress:
        resolved_chapter_id = chapter_id
        if resolved_chapter_id is None:
            # No chapter context given - best-effort guess from the most
            # recent match (fine for content accessed outside any chapter
            # flow, e.g. straight from the library).
            match = (
                db.query(models.ContentMatch)
                .filter(models.ContentMatch.content_item_id == content_item.id)
                .order_by(models.ContentMatch.id.desc())
                .first()
            )
            resolved_chapter_id = match.chapter_id if match else None

        progress = models.ContentProgress(
            owner_type=owner_type,
            owner_id=owner_id,
            content_item_id=content_item.id,
            chapter_id=resolved_chapter_id,
            status="not_started",
            progress_percent=0,
        )
        db.add(progress)
        db.flush()
    return progress


# =========================================================
# Week 8: Content access (starts/resumes consumption)
# =========================================================
@router.post(
    "/access/{content_item_id}",
    response_model=schemas.ContentAccessOut,
    summary="Open a content item - returns the content plus where to resume from",
)
def access_content(
    content_item_id: int,
    chapter_id: Optional[int] = Query(None, description="Which chapter you're viewing this content for (a shared library item can belong to several chapters)"),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    item = db.query(models.ContentItem).filter(models.ContentItem.id == content_item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content item not found")

    progress = get_or_create_progress(db, "user", current_user.id, item, chapter_id=chapter_id)
    if progress.status == "not_started":
        progress.status = "in_progress"
    db.commit()
    db.refresh(progress)

    return schemas.ContentAccessOut(
        content=_content_item_out(item),
        status=progress.status,
        progress_percent=progress.progress_percent,
        last_position_seconds=progress.last_position_seconds,
    )


# =========================================================
# Week 8: Resume-progress tracking (update as the learner consumes it)
# =========================================================
@router.patch(
    "/{content_item_id}",
    response_model=schemas.ProgressOut,
    summary="Update progress on a content item (percent complete / resume position / mark completed)",
)
def update_progress(
    content_item_id: int,
    payload: schemas.ProgressUpdate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    item = db.query(models.ContentItem).filter(models.ContentItem.id == content_item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content item not found")

    progress = get_or_create_progress(db, "user", current_user.id, item)

    if payload.status is not None:
        if payload.status not in ("in_progress", "completed", "not_started"):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid status")
        progress.status = payload.status
        if payload.status == "completed":
            progress.progress_percent = 100
    if payload.progress_percent is not None:
        progress.progress_percent = payload.progress_percent
        if progress.progress_percent >= 100:
            progress.status = "completed"
    if payload.last_position_seconds is not None:
        progress.last_position_seconds = payload.last_position_seconds

    db.commit()
    db.refresh(progress)

    return schemas.ProgressOut(
        content_item_id=progress.content_item_id,
        chapter_id=progress.chapter_id,
        status=progress.status,
        progress_percent=progress.progress_percent,
        last_position_seconds=progress.last_position_seconds,
        updated_at=progress.updated_at,
    )


# =========================================================
# Week 9: Completed chapters
# =========================================================
@router.get(
    "/completed-chapters",
    response_model=list[schemas.CompletedChapterOut],
    summary="List chapters where at least one content item has been completed",
)
def completed_chapters(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    completed = (
        db.query(models.ContentProgress)
        .filter(
            models.ContentProgress.owner_type == "user",
            models.ContentProgress.owner_id == current_user.id,
            models.ContentProgress.status == "completed",
            models.ContentProgress.chapter_id.isnot(None),
        )
        .order_by(models.ContentProgress.updated_at.desc())
        .all()
    )

    seen_chapters = set()
    results = []
    for p in completed:
        if p.chapter_id in seen_chapters:
            continue
        seen_chapters.add(p.chapter_id)
        chapter = db.query(models.Chapter).filter(models.Chapter.id == p.chapter_id).first()
        if chapter:
            results.append(schemas.CompletedChapterOut(
                chapter_id=chapter.id,
                chapter_name=chapter.chapter_name,
                subject=chapter.subject,
                class_name=chapter.class_name,
                completed_at=p.updated_at,
            ))
    return results


# =========================================================
# Week 9: Continue learning
# =========================================================
@router.get(
    "/continue-learning",
    response_model=list[schemas.ProgressOut],
    summary="List content items the learner started but hasn't finished, most recent first",
)
def continue_learning(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(models.ContentProgress)
        .filter(
            models.ContentProgress.owner_type == "user",
            models.ContentProgress.owner_id == current_user.id,
            models.ContentProgress.status == "in_progress",
        )
        .order_by(models.ContentProgress.updated_at.desc())
        .all()
    )
    results = []
    for p in rows:
        item = db.query(models.ContentItem).filter(models.ContentItem.id == p.content_item_id).first()
        results.append(schemas.ProgressOut(
            content_item_id=p.content_item_id,
            chapter_id=p.chapter_id,
            status=p.status,
            progress_percent=p.progress_percent,
            last_position_seconds=p.last_position_seconds,
            updated_at=p.updated_at,
            content=_content_item_out(item) if item else None,
        ))
    return results


# =========================================================
# Week 9: Learning history
# =========================================================
@router.get(
    "/history",
    response_model=list[schemas.ProgressOut],
    summary="Full chronological progress history for the learner",
)
def learning_history(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(models.ContentProgress)
        .filter(
            models.ContentProgress.owner_type == "user",
            models.ContentProgress.owner_id == current_user.id,
        )
        .order_by(models.ContentProgress.updated_at.desc())
        .all()
    )
    results = []
    for p in rows:
        item = db.query(models.ContentItem).filter(models.ContentItem.id == p.content_item_id).first()
        results.append(schemas.ProgressOut(
            content_item_id=p.content_item_id,
            chapter_id=p.chapter_id,
            status=p.status,
            progress_percent=p.progress_percent,
            last_position_seconds=p.last_position_seconds,
            updated_at=p.updated_at,
            content=_content_item_out(item) if item else None,
        ))
    return results
