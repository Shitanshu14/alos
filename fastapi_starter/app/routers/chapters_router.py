"""
Week 3 — Chapter Upload.
Week 4 — Chapter processing (text extraction -> AI understanding -> content brief).

Lets a logged-in teacher/parent upload a school chapter (PDF or DOCX)
along with its class and subject. The file is saved to disk and a
Chapter row is created with status="uploaded". Right after that, a
background task picks the chapter up and moves it through
"processing" -> "ready" (or "failed"), producing a ChapterBrief along
the way (summary + key concepts + suggested content types) that Week
5/6 (content library + matching) will use next.
"""
import json
import logging
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File, Form, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError, OperationalError

from .. import models, schemas
from ..database import get_db, SessionLocal
from ..config import settings
from ..services.text_extraction import extract_text, TextExtractionError
from ..services.ai_service import analyze_chapter_text, AIServiceError
from .auth_router import get_current_user

logger = logging.getLogger("app.chapters")

router = APIRouter(prefix="/chapters", tags=["Chapters"])


def _ensure_upload_dir() -> Path:
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def _process_chapter(chapter_id: int) -> None:
    """Week 4 pipeline, run as a background task right after upload:
    extract text -> ask the AI to understand it -> save a ChapterBrief.
    Uses its own DB session since it runs outside the request lifecycle."""
    db = SessionLocal()
    try:
        chapter = db.query(models.Chapter).filter(models.Chapter.id == chapter_id).first()
        if not chapter:
            logger.warning("Chapter %s disappeared before processing could start", chapter_id)
            return

        chapter.status = "processing"
        chapter.status_message = None
        db.commit()

        try:
            chapter_text = extract_text(chapter.file_path, chapter.file_type)
            analysis = analyze_chapter_text(chapter_text)
        except (TextExtractionError, AIServiceError) as exc:
            chapter.status = "failed"
            chapter.status_message = str(exc)
            db.commit()
            logger.warning("Chapter %s processing failed: %s", chapter_id, exc)
            return

        # Replace any previous brief (e.g. if this chapter is reprocessed)
        existing_brief = (
            db.query(models.ChapterBrief)
            .filter(models.ChapterBrief.chapter_id == chapter_id)
            .first()
        )
        if existing_brief:
            db.delete(existing_brief)
            db.flush()

        db.add(models.ChapterBrief(
            chapter_id=chapter_id,
            summary=analysis["summary"],
            key_concepts=json.dumps(analysis["key_concepts"]),
            suggested_content_types=json.dumps(analysis.get("suggested_content_types", [])),
        ))
        chapter.status = "ready"
        chapter.status_message = "Chapter understood - ready for content matching"
        db.commit()
        logger.info("Chapter %s processed successfully", chapter_id)

    except Exception:
        db.rollback()
        logger.exception("Unexpected error while processing chapter %s", chapter_id)
        try:
            chapter = db.query(models.Chapter).filter(models.Chapter.id == chapter_id).first()
            if chapter:
                chapter.status = "failed"
                chapter.status_message = "Unexpected error during processing"
                db.commit()
        except Exception:
            db.rollback()
    finally:
        db.close()


@router.post(
    "/upload",
    response_model=schemas.ChapterOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a chapter (PDF/DOCX) to start the ALOS pipeline",
)
async def upload_chapter(
    background_tasks: BackgroundTasks,
    class_name: str = Form(..., description="e.g. 'Class 6'"),
    subject: str = Form(..., description="e.g. 'Science'"),
    chapter_name: str = Form(..., description="e.g. 'Photosynthesis'"),
    file: UploadFile = File(..., description="The chapter file - PDF or DOCX only"),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # --- Validate basic text fields aren't empty/whitespace ---
    class_name = class_name.strip()
    subject = subject.strip()
    chapter_name = chapter_name.strip()
    if not class_name or not subject or not chapter_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="class_name, subject, and chapter_name cannot be empty",
        )

    # --- Validate file type ---
    original_filename = file.filename or "chapter"
    ext = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else ""
    if ext not in schemas.ALLOWED_CHAPTER_FILE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Only these file types are allowed: {', '.join(sorted(schemas.ALLOWED_CHAPTER_FILE_TYPES))}",
        )

    # --- Read the file and validate size ---
    contents = await file.read()
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(contents) == 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Uploaded file is empty")
    if len(contents) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File is too large. Maximum allowed size is {settings.MAX_UPLOAD_SIZE_MB} MB",
        )

    # --- Save the file to disk with a unique, collision-proof name ---
    upload_dir = _ensure_upload_dir()
    safe_name = f"{uuid.uuid4().hex}.{ext}"
    file_path = upload_dir / safe_name

    try:
        with open(file_path, "wb") as f:
            f.write(contents)
    except OSError:
        logger.exception("Failed to write uploaded chapter file to disk")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not save the uploaded file. Please try again in a moment.",
        )

    # --- Create the Chapter record ---
    new_chapter = models.Chapter(
        uploaded_by=current_user.id,
        class_name=class_name,
        subject=subject,
        chapter_name=chapter_name,
        original_filename=original_filename,
        file_path=str(file_path),
        file_type=ext,
        file_size_bytes=len(contents),
        status="uploaded",
    )
    db.add(new_chapter)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # The file is already saved on disk at this point but the DB row
        # failed - clean up the orphaned file so disk doesn't fill up.
        os.remove(file_path) if file_path.exists() else None
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Could not save chapter record")
    except OperationalError:
        db.rollback()
        os.remove(file_path) if file_path.exists() else None
        logger.exception("Database was unreachable while saving a chapter")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is temporarily unavailable. Please try again in a moment.",
        )
    db.refresh(new_chapter)

    # Kick off Week 4 processing (text extraction -> AI understanding) in the
    # background so the upload response returns immediately.
    background_tasks.add_task(_process_chapter, new_chapter.id)

    return new_chapter


@router.get(
    "/{chapter_id}/status",
    response_model=schemas.ChapterStatusOut,
    summary="Check a chapter's processing status",
)
def get_chapter_status(
    chapter_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    chapter = db.query(models.Chapter).filter(models.Chapter.id == chapter_id).first()
    if not chapter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chapter not found")
    if chapter.uploaded_by != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You don't have access to this chapter")
    return chapter


@router.get(
    "/{chapter_id}/brief",
    response_model=schemas.ChapterBriefOut,
    summary="Get the AI-generated content brief for a processed chapter",
)
def get_chapter_brief(
    chapter_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    chapter = db.query(models.Chapter).filter(models.Chapter.id == chapter_id).first()
    if not chapter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chapter not found")
    if chapter.uploaded_by != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You don't have access to this chapter")

    brief = (
        db.query(models.ChapterBrief)
        .filter(models.ChapterBrief.chapter_id == chapter_id)
        .first()
    )
    if not brief:
        detail = (
            "Chapter processing failed - see the /status endpoint for details."
            if chapter.status == "failed"
            else "Chapter brief isn't ready yet - processing may still be in progress."
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)

    return schemas.ChapterBriefOut(
        chapter_id=brief.chapter_id,
        summary=brief.summary,
        key_concepts=json.loads(brief.key_concepts),
        suggested_content_types=json.loads(brief.suggested_content_types or "[]"),
        created_at=brief.created_at,
    )


@router.get(
    "/",
    response_model=list[schemas.ChapterOut],
    summary="List chapters uploaded by the current user",
)
def list_my_chapters(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(models.Chapter)
        .filter(models.Chapter.uploaded_by == current_user.id)
        .order_by(models.Chapter.created_at.desc())
        .all()
    )
