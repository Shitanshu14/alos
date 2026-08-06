"""
Week 5 — Content Library.

A library of ready-made learning content (video/story/poem/cartoon),
each tagged by subject, class, and concept tags. Week 6 (matching) will
search this table to find the best existing item for a chapter before
falling back to generating something new (Week 7).

- Any logged-in user can browse/search the library (GET endpoints).
- Only admins can add/update/delete items (POST/PUT/DELETE) - content
  curation is an internal/ops task, not something teachers or parents do.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from .. import models, schemas
from ..database import get_db
from .auth_router import get_current_user, require_admin
from ..seed_content_library import seed_content_library

logger = logging.getLogger("app.content_library")

router = APIRouter(prefix="/content-library", tags=["Content Library"])


def _get_or_create_tags(db: Session, tag_names: list[str]) -> list[models.ContentTag]:
    if not tag_names:
        return []
    existing = (
        db.query(models.ContentTag)
        .filter(models.ContentTag.name.in_(tag_names))
        .all()
    )
    existing_names = {t.name for t in existing}
    for name in tag_names:
        if name not in existing_names:
            new_tag = models.ContentTag(name=name)
            db.add(new_tag)
            existing.append(new_tag)
            existing_names.add(name)
    return existing


def _to_out(item: models.ContentItem) -> schemas.ContentItemOut:
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
        tags=[t.name for t in item.tags],
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


# =========================================================
# CREATE (admin only)
# =========================================================
@router.post(
    "/",
    response_model=schemas.ContentItemOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a new content library item (admin only)",
)
def create_content_item(
    payload: schemas.ContentItemCreate,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    tags = _get_or_create_tags(db, payload.tags)

    item = models.ContentItem(
        title=payload.title,
        content_type=payload.content_type,
        subject=payload.subject,
        class_name=payload.class_name,
        description=payload.description,
        body=payload.body,
        media_url=payload.media_url,
        is_seed_placeholder=False,
        created_by=admin.id,
        tags=tags,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _to_out(item)


# =========================================================
# LIST / SEARCH (any logged-in user)
# =========================================================
@router.get(
    "/",
    response_model=list[schemas.ContentItemOut],
    summary="Browse/search the content library",
)
def list_content_items(
    subject: Optional[str] = Query(None, description="Filter by subject, e.g. 'Science'"),
    class_name: Optional[str] = Query(None, description="Filter by class, e.g. 'Class 6'"),
    content_type: Optional[str] = Query(None, description="Filter by type: video/story/poem/cartoon"),
    tag: Optional[str] = Query(None, description="Filter by a single concept tag, e.g. 'photosynthesis'"),
    search: Optional[str] = Query(None, description="Free-text search in title/description"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(models.ContentItem).options(joinedload(models.ContentItem.tags))

    if subject:
        q = q.filter(models.ContentItem.subject.ilike(subject))
    if class_name:
        q = q.filter(models.ContentItem.class_name.ilike(class_name))
    if content_type:
        q = q.filter(models.ContentItem.content_type == content_type.strip().lower())
    if tag:
        q = q.join(models.ContentItem.tags).filter(models.ContentTag.name == tag.strip().lower())
    if search:
        like = f"%{search}%"
        q = q.filter(
            (models.ContentItem.title.ilike(like)) | (models.ContentItem.description.ilike(like))
        )

    items = (
        q.order_by(models.ContentItem.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_to_out(i) for i in items]


# =========================================================
# GET single item
# =========================================================
@router.get(
    "/{item_id}",
    response_model=schemas.ContentItemOut,
    summary="Get a single content library item",
)
def get_content_item(
    item_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    item = (
        db.query(models.ContentItem)
        .options(joinedload(models.ContentItem.tags))
        .filter(models.ContentItem.id == item_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content item not found")
    return _to_out(item)


# =========================================================
# UPDATE (admin only)
# =========================================================
@router.put(
    "/{item_id}",
    response_model=schemas.ContentItemOut,
    summary="Update a content library item (admin only)",
)
def update_content_item(
    item_id: int,
    payload: schemas.ContentItemUpdate,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.query(models.ContentItem).filter(models.ContentItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content item not found")

    update_data = payload.model_dump(exclude_unset=True, exclude={"tags"})
    for field, value in update_data.items():
        setattr(item, field, value)

    if payload.tags is not None:
        item.tags = _get_or_create_tags(db, payload.tags)

    # Once an admin edits a seeded placeholder, it's no longer a placeholder -
    # it's been reviewed/curated.
    item.is_seed_placeholder = False

    db.commit()
    db.refresh(item)
    return _to_out(item)


# =========================================================
# DELETE (admin only)
# =========================================================
@router.delete(
    "/{item_id}",
    response_model=schemas.MessageResponse,
    summary="Delete a content library item (admin only)",
)
def delete_content_item(
    item_id: int,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    item = db.query(models.ContentItem).filter(models.ContentItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content item not found")
    db.delete(item)
    db.commit()
    return {"message": "Content item deleted"}


# =========================================================
# SEED (admin only) - bulk-populate the library with starter content
# =========================================================
@router.post(
    "/seed",
    response_model=schemas.MessageResponse,
    summary="Bulk-populate the library with starter content (admin only, no-op if already seeded)",
)
def seed_library(
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    inserted = seed_content_library(db)
    if inserted == 0:
        return {"message": "Content library already has items - nothing was inserted."}
    return {"message": f"Inserted {inserted} content library item(s)."}
