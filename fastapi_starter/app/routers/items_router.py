from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from .. import models, schemas
from ..database import get_db
from .auth_router import get_current_user

router = APIRouter(prefix="/items", tags=["Items"])


# CREATE
@router.post("/", response_model=schemas.ItemOut)
def create_item(item: schemas.ItemCreate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    new_item = models.Item(**item.dict(), owner_id=current_user.id)
    db.add(new_item)
    db.commit()
    db.refresh(new_item)
    return new_item


# READ ALL
@router.get("/", response_model=List[schemas.ItemOut])
def list_items(
    skip: int = 0,
    limit: int = 10,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    return db.query(models.Item).filter(models.Item.owner_id == current_user.id).offset(skip).limit(limit).all()


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
