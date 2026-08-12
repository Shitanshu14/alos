"""
Week 13 — Concept-matching game + memory game data endpoints.

Both games are generated from a chapter's key concepts (Week 4) and
cached in GameTemplate so repeat requests are instant. Access follows
the same rule as chapters: the uploader, or a student with homework
pointing at this chapter (see access_control_service.py).
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services.access_control_service import can_access_chapter
from ..services.game_generation_service import build_concept_match_pairs, build_memory_cards, serialize, deserialize
from .auth_router import get_current_user

logger = logging.getLogger("app.games")

router = APIRouter(prefix="/games", tags=["Games"])


def _get_chapter_and_brief(chapter_id: int, current_user: models.User, db: Session):
    chapter = db.query(models.Chapter).filter(models.Chapter.id == chapter_id).first()
    if not chapter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chapter not found")
    if not can_access_chapter(db, chapter, current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You don't have access to this chapter")

    brief = db.query(models.ChapterBrief).filter(models.ChapterBrief.chapter_id == chapter_id).first()
    if not brief:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This chapter hasn't finished processing yet - no concepts available")

    return chapter, brief


def _get_or_create_template(db: Session, chapter_id: int, game_type: str, build_fn) -> list:
    existing = (
        db.query(models.GameTemplate)
        .filter(models.GameTemplate.chapter_id == chapter_id, models.GameTemplate.game_type == game_type)
        .first()
    )
    if existing:
        return deserialize(existing.data)

    data = build_fn()
    db.add(models.GameTemplate(chapter_id=chapter_id, game_type=game_type, data=serialize(data)))
    db.commit()
    return data


@router.get(
    "/concept-match/{chapter_id}",
    response_model=schemas.ConceptMatchGameOut,
    summary="Get concept-matching game data for a chapter (concept -> clue pairs)",
)
def concept_match_game(
    chapter_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    chapter, brief = _get_chapter_and_brief(chapter_id, current_user, db)
    key_concepts = json.loads(brief.key_concepts)

    pairs = _get_or_create_template(
        db, chapter_id, "concept_match",
        lambda: build_concept_match_pairs(chapter, key_concepts, brief.summary),
    )
    return schemas.ConceptMatchGameOut(chapter_id=chapter.id, chapter_name=chapter.chapter_name, pairs=pairs)


@router.get(
    "/memory/{chapter_id}",
    response_model=schemas.MemoryGameOut,
    summary="Get memory (flip-card) game data for a chapter",
)
def memory_game(
    chapter_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    chapter, brief = _get_chapter_and_brief(chapter_id, current_user, db)
    key_concepts = json.loads(brief.key_concepts)

    cards = _get_or_create_template(
        db, chapter_id, "memory",
        lambda: build_memory_cards(key_concepts),
    )
    return schemas.MemoryGameOut(chapter_id=chapter.id, chapter_name=chapter.chapter_name, cards=cards)
