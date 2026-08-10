"""
Week 11 — Public share link resolution + "shared with me" view.

GET /share/{token} is deliberately public (no login required) - that's
the whole point of a share link. It only ever returns the safe-to-show
fields (no internal IDs, no owner info).

GET /shared-with-me is for a logged-in student to see content a teacher
sent them via classroom sharing (content_library_router.py's
/share-classroom endpoint).
"""
import datetime
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from .auth_router import get_current_user

logger = logging.getLogger("app.sharing")

router = APIRouter(tags=["Sharing"])


@router.get(
    "/share/{token}",
    response_model=schemas.SharedContentView,
    summary="View a shared content item via its public link (no login required)",
)
def resolve_share_link(token: str, db: Session = Depends(get_db)):
    link = db.query(models.ShareLink).filter(models.ShareLink.token == token).first()
    if not link:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This share link doesn't exist")

    if link.expires_at and link.expires_at < datetime.datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This share link has expired")

    item = db.query(models.ContentItem).filter(models.ContentItem.id == link.content_item_id).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The shared content no longer exists")

    link.view_count += 1
    db.commit()

    return schemas.SharedContentView(
        title=item.title,
        content_type=item.content_type,
        description=item.description,
        body=item.body,
        media_url=item.media_url,
        subject=item.subject,
        class_name=item.class_name,
    )


@router.get(
    "/shared-with-me",
    response_model=list[schemas.SharedContentOut],
    summary="List content shared with me (e.g. by a teacher via classroom sharing)",
)
def shared_with_me(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(models.SharedContent)
        .filter(models.SharedContent.target_type == "user", models.SharedContent.target_id == current_user.id)
        .order_by(models.SharedContent.created_at.desc())
        .all()
    )
    results = []
    for r in rows:
        item = db.query(models.ContentItem).filter(models.ContentItem.id == r.content_item_id).first()
        content_out = None
        if item:
            content_out = schemas.ContentItemOut(
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
        results.append(schemas.SharedContentOut(
            id=r.id,
            content_item_id=r.content_item_id,
            content=content_out,
            message=r.message,
            created_at=r.created_at,
        ))
    return results
