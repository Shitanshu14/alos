import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from .auth_router import get_current_user

router = APIRouter(prefix="/items", tags=["Items"])

ALLOWED_SORT_FIELDS = {"created_at", "title", "updated_at"}


# CREATE
@router.post("/", response_model=schemas.ItemOut)
def create_item(item: schemas.ItemCreate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    new_item = models.Item(**item.dict(), owner_id=current_user.id)
    db.add(new_item)
    db.commit()
    db.refresh(new_item)
    return new_item


# READ ALL - with search, sorting, and filtering
@router.get("/", response_model=List[schemas.ItemOut])
def list_items(
    skip: int = 0,
    limit: int = 10,
    search: Optional[str] = Query(None, description="Title/description me search karo, e.g. ?search=laptop"),
    sort: Optional[str] = Query(
        None,
        description="Sort field. Options: created_at, -created_at, title, -title, updated_at, -updated_at "
                    "('-' prefix = descending order)",
    ),
    owner_id: Optional[int] = Query(None, description="Filter by owner_id"),
    created_after: Optional[datetime.datetime] = Query(None, description="ISO format, e.g. 2026-01-01T00:00:00"),
    created_before: Optional[datetime.datetime] = Query(None, description="ISO format, e.g. 2026-12-31T23:59:59"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    query = db.query(models.Item).filter(models.Item.owner_id == current_user.id)

    # NOTE: owner_id filter yahan sirf current user ke items tak restrict rehta hai
    # (har user sirf apna data dekh sakta hai) - agar owner_id diya gaya hai to
    # bas current user ke apne id se match hona chahiye, warna empty result.
    if owner_id is not None:
        query = query.filter(models.Item.owner_id == owner_id)

    if search:
        like_pattern = f"%{search}%"
        query = query.filter(
            (models.Item.title.ilike(like_pattern)) | (models.Item.description.ilike(like_pattern))
        )

    if created_after:
        query = query.filter(models.Item.created_at >= created_after)
    if created_before:
        query = query.filter(models.Item.created_at <= created_before)

    if sort:
        descending = sort.startswith("-")
        field_name = sort.lstrip("-")
        if field_name not in ALLOWED_SORT_FIELDS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid sort field '{field_name}'. Allowed: {', '.join(sorted(ALLOWED_SORT_FIELDS))}",
            )
        column = getattr(models.Item, field_name)
        query = query.order_by(column.desc() if descending else column.asc())
    else:
        query = query.order_by(models.Item.created_at.desc())

    return query.offset(skip).limit(limit).all()


# READ ONE
@router.get("/{item_id}", response_model=schemas.ItemOut)
def get_item(item_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    item = db.query(models.Item).filter(models.Item.id == item_id, models.Item.owner_id == current_user.id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


# UPDATE
@router.put("/{item_id}", response_model=schemas.ItemOut)
def update_item(item_id: int, updated: schemas.ItemCreate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    item = db.query(models.Item).filter(models.Item.id == item_id, models.Item.owner_id == current_user.id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    item.title = updated.title
    item.description = updated.description
    db.commit()
    db.refresh(item)
    return item


# DELETE
@router.delete("/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    item = db.query(models.Item).filter(models.Item.id == item_id, models.Item.owner_id == current_user.id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete(item)
    db.commit()
    return {"message": "Item deleted successfully"}
