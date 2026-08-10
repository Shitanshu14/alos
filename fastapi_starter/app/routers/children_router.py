"""
Week 10 — Parent-child.

Lets a parent add child profiles, set their class, and view a learning
report for each child. Only accessible to users with role="parent".

Honesty note: children don't have their own login in this phase of
ALOS, so nothing currently *writes* ContentProgress rows with
owner_type="child" - a child's learning report will be empty until a
future "child device/session" flow is built to actually record their
activity. The read side (this router) is ready for that data the
moment it starts existing.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services.notification_service import notify
from .auth_router import get_current_user

router = APIRouter(prefix="/children", tags=["Children"])


def _require_parent(current_user: models.User) -> None:
    if current_user.role != "parent":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only parent accounts can manage children")


def _get_owned_child(child_id: int, current_user: models.User, db: Session) -> models.Child:
    child = db.query(models.Child).filter(models.Child.id == child_id).first()
    if not child:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Child not found")
    if child.parent_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This child doesn't belong to your account")
    return child


# =========================================================
# Add child
# =========================================================
@router.post("/", response_model=schemas.ChildOut, status_code=status.HTTP_201_CREATED, summary="Add a child profile")
def add_child(
    payload: schemas.ChildCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_parent(current_user)
    child = models.Child(parent_id=current_user.id, name=payload.name, class_name=payload.class_name)
    db.add(child)
    db.commit()
    db.refresh(child)
    return child


# =========================================================
# List my children
# =========================================================
@router.get("/", response_model=list[schemas.ChildOut], summary="List my children")
def list_children(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_parent(current_user)
    return (
        db.query(models.Child)
        .filter(models.Child.parent_id == current_user.id)
        .order_by(models.Child.created_at.asc())
        .all()
    )


# =========================================================
# Child class-selection endpoint
# =========================================================
@router.patch("/{child_id}/class", response_model=schemas.ChildOut, summary="Set/update a child's class")
def set_child_class(
    child_id: int,
    payload: schemas.ChildClassUpdate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_parent(current_user)
    child = _get_owned_child(child_id, current_user, db)
    child.class_name = payload.class_name
    db.commit()
    db.refresh(child)
    return child


# =========================================================
# Parent view of child's learning/completion
# =========================================================
@router.get(
    "/{child_id}/progress",
    response_model=schemas.ChildProgressReportOut,
    summary="View a child's learning progress and completed chapters",
)
def child_progress_report(
    child_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_parent(current_user)
    child = _get_owned_child(child_id, current_user, db)

    completed_rows = (
        db.query(models.ContentProgress)
        .filter(
            models.ContentProgress.owner_type == "child",
            models.ContentProgress.owner_id == child.id,
            models.ContentProgress.status == "completed",
            models.ContentProgress.chapter_id.isnot(None),
        )
        .order_by(models.ContentProgress.updated_at.desc())
        .all()
    )
    seen_chapters = set()
    completed_chapters = []
    for p in completed_rows:
        if p.chapter_id in seen_chapters:
            continue
        seen_chapters.add(p.chapter_id)
        chapter = db.query(models.Chapter).filter(models.Chapter.id == p.chapter_id).first()
        if chapter:
            completed_chapters.append(schemas.CompletedChapterOut(
                chapter_id=chapter.id,
                chapter_name=chapter.chapter_name,
                subject=chapter.subject,
                class_name=chapter.class_name,
                completed_at=p.updated_at,
            ))

    in_progress_rows = (
        db.query(models.ContentProgress)
        .filter(
            models.ContentProgress.owner_type == "child",
            models.ContentProgress.owner_id == child.id,
            models.ContentProgress.status == "in_progress",
        )
        .order_by(models.ContentProgress.updated_at.desc())
        .all()
    )
    in_progress = [
        schemas.ProgressOut(
            content_item_id=p.content_item_id,
            chapter_id=p.chapter_id,
            status=p.status,
            progress_percent=p.progress_percent,
            last_position_seconds=p.last_position_seconds,
            updated_at=p.updated_at,
        )
        for p in in_progress_rows
    ]

    return schemas.ChildProgressReportOut(
        child=child,
        completed_chapters=completed_chapters,
        in_progress=in_progress,
    )


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


# =========================================================
# Week 11: Send-to-child
# =========================================================
@router.post(
    "/{child_id}/send",
    response_model=schemas.SharedContentOut,
    status_code=status.HTTP_201_CREATED,
    summary="Send a content item to a child (parent only)",
)
def send_to_child(
    child_id: int,
    payload: schemas.SendToChildRequest,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_parent(current_user)
    child = _get_owned_child(child_id, current_user, db)

    item = db.query(models.ContentItem).filter(models.ContentItem.id == payload.content_item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content item not found")

    shared = models.SharedContent(
        content_item_id=item.id,
        shared_by=current_user.id,
        target_type="child",
        target_id=child.id,
        message=payload.message,
    )
    db.add(shared)
    db.commit()
    db.refresh(shared)

    return schemas.SharedContentOut(
        id=shared.id,
        content_item_id=shared.content_item_id,
        content=_content_item_out(item),
        message=shared.message,
        created_at=shared.created_at,
    )


# =========================================================
# Week 11: What's been shared with this child
# =========================================================
@router.get(
    "/{child_id}/shared-content",
    response_model=list[schemas.SharedContentOut],
    summary="List content that's been sent to a child (parent only)",
)
def list_shared_with_child(
    child_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_parent(current_user)
    child = _get_owned_child(child_id, current_user, db)

    rows = (
        db.query(models.SharedContent)
        .filter(models.SharedContent.target_type == "child", models.SharedContent.target_id == child.id)
        .order_by(models.SharedContent.created_at.desc())
        .all()
    )
    results = []
    for r in rows:
        item = db.query(models.ContentItem).filter(models.ContentItem.id == r.content_item_id).first()
        results.append(schemas.SharedContentOut(
            id=r.id,
            content_item_id=r.content_item_id,
            content=_content_item_out(item) if item else None,
            message=r.message,
            created_at=r.created_at,
        ))
    return results
