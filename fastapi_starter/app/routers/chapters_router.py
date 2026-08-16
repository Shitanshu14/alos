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

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, UploadFile, File, Form, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import IntegrityError, OperationalError

from .. import models, schemas
from ..database import get_db, SessionLocal
from ..config import settings
from ..services.text_extraction import extract_text, TextExtractionError
from ..services.ai_service import analyze_chapter_text, AIServiceError
from ..services.matching_service import find_best_matches, evaluate_matches, get_unmatched_content_types, CONTENT_TYPES
from ..services.customization_service import customize_for_chapter
from ..services.content_generation_service import decide_content_types, generate_content, validate_generated_content
from ..services.video_rendering_service import render_video, VideoRenderingError
from ..services.notification_service import notify
from ..services.access_control_service import can_access_chapter
from ..rate_limiter import limiter
from .auth_router import get_current_user

logger = logging.getLogger("app.chapters")

router = APIRouter(prefix="/chapters", tags=["Chapters"])


def _ensure_upload_dir() -> Path:
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def _run_matching(db: Session, chapter: models.Chapter, key_concepts: list) -> None:
    """Week 6+7 pipeline step, called from _process_chapter right after the
    chapter brief is generated:
      1. Find the best library match for each content type (Week 6).
      2. Quality-check each match; detect which types have no valid match
         (Week 7 - no-match detection).
      3. For those types, decide priority by age/subject and generate new
         content locally, then push it into the library (Week 7).
    Saves one ContentMatch row per content type either way."""
    # Clear any previous matches (e.g. if this chapter is reprocessed)
    db.query(models.ContentMatch).filter(models.ContentMatch.chapter_id == chapter.id).delete()

    raw_matches = find_best_matches(db, chapter, key_concepts)
    evaluated = evaluate_matches(raw_matches)
    unmatched_types = get_unmatched_content_types(evaluated)

    # Content type decision logic (age/subject based) - used to log/prioritize;
    # generation still covers every unmatched slot so the chapter ends up
    # with all 4 content types available.
    brief = db.query(models.ChapterBrief).filter(models.ChapterBrief.chapter_id == chapter.id).first()
    suggested_types = json.loads(brief.suggested_content_types) if brief and brief.suggested_content_types else []
    priority_order = decide_content_types(chapter, suggested_types)
    logger.info("Chapter %s content type priority: %s", chapter.id, priority_order)

    for content_type in CONTENT_TYPES:
        result = evaluated[content_type]

        if result["is_valid"]:
            custom = customize_for_chapter(result["item"], chapter, result["matched_concepts"])
            db.add(models.ContentMatch(
                chapter_id=chapter.id,
                content_type=content_type,
                content_item_id=result["item"].id,
                match_score=result["score"],
                match_status="matched",
                quality_notes=result["reason"],
                matched_concepts=json.dumps(result["matched_concepts"]),
                customized_title=custom["title"],
                customized_description=custom["description"],
            ))
            continue

        # --- Week 7: no match found - generate new content instead ---
        generated = generate_content(chapter, content_type, key_concepts)
        is_valid, gen_reason = validate_generated_content(generated)

        if not is_valid:
            db.add(models.ContentMatch(
                chapter_id=chapter.id,
                content_type=content_type,
                content_item_id=None,
                match_score=result["score"],
                match_status="failed_quality",
                quality_notes=f"Library: {result['reason']} | Generation: {gen_reason}",
                matched_concepts=json.dumps(result["matched_concepts"]),
            ))
            continue

        # --- Week 7 extended: for "video", try to actually render a real .mp4 ---
        if content_type == "video":
            try:
                relative_path = render_video(chapter.chapter_name, generated["body"], settings.GENERATED_MEDIA_DIR)
                generated["media_url"] = f"/media/{relative_path}"
                generated["title"] = generated["title"].replace(" (Script)", "")
                generated["description"] = (
                    f"An auto-generated narrated video covering {chapter.chapter_name}'s key concepts."
                )
                generated["body"] = generated["body"].replace(
                    "[Auto-generated script - record/animate an actual video from this before publishing to students.]",
                    "[This script was rendered into the video above (narrated slideshow, auto-generated locally).]",
                )
                gen_reason += " | Rendered as a real video (narrated slideshow)."
            except VideoRenderingError as exc:
                logger.warning("Video rendering failed for chapter %s, falling back to script only: %s", chapter.id, exc)
                gen_reason += f" | Video rendering unavailable, kept as script only ({exc})."

        new_item = models.ContentItem(
            title=generated["title"],
            content_type=content_type,
            subject=chapter.subject,
            class_name=chapter.class_name,
            description=generated["description"],
            body=generated["body"],
            media_url=generated["media_url"],
            is_seed_placeholder=False,
            is_generated=True,
            generated_for_chapter_id=chapter.id,
            created_by=None,
        )
        # Tag the new item with the chapter's own key concepts so it's
        # discoverable for future chapters covering the same ground.
        tag_names = {c.strip().lower() for c in key_concepts if c.strip()}
        tag_names.add(chapter.subject.strip().lower())
        tag_names.add(chapter.class_name.strip().lower())
        tags = []
        for name in tag_names:
            tag = db.query(models.ContentTag).filter(models.ContentTag.name == name).first()
            if not tag:
                tag = models.ContentTag(name=name)
                db.add(tag)
                db.flush()
            tags.append(tag)
        new_item.tags = tags

        db.add(new_item)
        db.flush()  # get new_item.id before referencing it

        db.add(models.ContentMatch(
            chapter_id=chapter.id,
            content_type=content_type,
            content_item_id=new_item.id,
            match_score=1.0,  # generated specifically for this chapter - perfect fit by definition
            match_status="generated",
            quality_notes=gen_reason,
            matched_concepts=json.dumps(key_concepts),
            customized_title=generated["title"],
            customized_description=generated["description"],
        ))

    db.flush()  # part of the same transaction as the rest of _process_chapter


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
            uploader = db.query(models.User).filter(models.User.id == chapter.uploaded_by).first()
            if uploader:
                notify(
                    db, uploader,
                    title="Chapter processing failed",
                    message=f"We couldn't process '{chapter.chapter_name}': {exc}",
                )
                db.commit()
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

        # --- Week 6+7: match the chapter against the content library, generate what's missing ---
        _run_matching(db, chapter, analysis["key_concepts"])

        chapter.status = "ready"
        chapter.status_message = "Chapter understood - ready for content matching"

        # --- Week 8: notify the uploader that their chapter is ready ---
        uploader = db.query(models.User).filter(models.User.id == chapter.uploaded_by).first()
        if uploader:
            notify(
                db, uploader,
                title="Your chapter is ready!",
                message=f"'{chapter.chapter_name}' has been processed and content is ready to view.",
            )

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
@limiter.limit(settings.CHAPTER_UPLOAD_RATE_LIMIT)
async def upload_chapter(
    request: Request,
    background_tasks: BackgroundTasks,
    class_name: str = Form(..., description="e.g. 'Class 6'"),
    subject: str = Form(..., description="e.g. 'Science'"),
    chapter_name: str = Form(..., description="e.g. 'Photosynthesis'"),
    file: UploadFile = File(..., description="The chapter file - PDF or DOCX only"),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Upload triggers an expensive background pipeline (text extraction,
    # matching, and possibly real video rendering via ffmpeg/TTS) - only
    # teacher/school accounts should be able to kick that off, matching
    # the feature's intended use (this also caps abuse/resource-exhaustion
    # risk, on top of the rate limit above).
    if current_user.role not in ("teacher", "school"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only teacher/school accounts can upload chapters",
        )
    # --- Validate basic text fields aren't empty/whitespace, and aren't
    # absurdly long (these get embedded into generated video slides,
    # notification text, etc. - an unbounded length is both a minor DoS
    # vector and can break the video slide layout) ---
    class_name = class_name.strip()
    subject = subject.strip()
    chapter_name = chapter_name.strip()
    if not class_name or not subject or not chapter_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="class_name, subject, and chapter_name cannot be empty",
        )
    if len(class_name) > 50 or len(subject) > 100 or len(chapter_name) > 200:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="class_name (max 50), subject (max 100), or chapter_name (max 200) is too long",
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
    if not can_access_chapter(db, chapter, current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You don't have access to this chapter")
    return chapter


@router.get(
    "/{chapter_id}/pipeline-status",
    response_model=schemas.ChapterPipelineStatusOut,
    summary="Detailed pipeline stage tracking for a chapter (Week 8)",
)
def get_chapter_pipeline_status(
    chapter_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    chapter = db.query(models.Chapter).filter(models.Chapter.id == chapter_id).first()
    if not chapter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chapter not found")
    if not can_access_chapter(db, chapter, current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You don't have access to this chapter")

    matches = db.query(models.ContentMatch).filter(models.ContentMatch.chapter_id == chapter_id).all()
    matched_count = sum(1 for m in matches if m.match_status == "matched")
    generated_count = sum(1 for m in matches if m.match_status == "generated")
    unresolved_count = sum(1 for m in matches if m.match_status not in ("matched", "generated"))

    if chapter.status == "failed":
        stage = "failed"
    elif chapter.status in ("uploaded", "processing"):
        stage = chapter.status
    elif matches and unresolved_count == 0:
        stage = "content_ready"  # all 4 content types resolved (matched or generated)
    else:
        stage = "ready"  # chapter understood, but matching/generation still incomplete

    return schemas.ChapterPipelineStatusOut(
        chapter_id=chapter.id,
        chapter_name=chapter.chapter_name,
        stage=stage,
        status_message=chapter.status_message,
        matched_count=matched_count,
        generated_count=generated_count,
        unresolved_count=unresolved_count,
    )


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
    if not can_access_chapter(db, chapter, current_user):
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
    "/{chapter_id}/matches",
    response_model=list[schemas.ContentMatchOut],
    summary="Get the content-library matches found for a processed chapter",
)
def get_chapter_matches(
    chapter_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    chapter = db.query(models.Chapter).filter(models.Chapter.id == chapter_id).first()
    if not chapter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chapter not found")
    if not can_access_chapter(db, chapter, current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You don't have access to this chapter")

    matches = (
        db.query(models.ContentMatch)
        .filter(models.ContentMatch.chapter_id == chapter_id)
        .all()
    )
    if not matches:
        detail = (
            "Chapter processing failed - see the /status endpoint for details."
            if chapter.status == "failed"
            else "Matches aren't ready yet - processing may still be in progress."
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)

    # Batch-fetch every matched content item (+ eager-load their tags) in
    # ONE query instead of one query per match row (was up to 4 separate
    # queries, plus another per-item query for tags).
    content_item_ids = [m.content_item_id for m in matches if m.content_item_id]
    items_by_id = {}
    if content_item_ids:
        items = (
            db.query(models.ContentItem)
            .options(joinedload(models.ContentItem.tags))
            .filter(models.ContentItem.id.in_(content_item_ids))
            .all()
        )
        items_by_id = {item.id: item for item in items}

    results = []
    for m in matches:
        content_item_out = None
        if m.content_item_id:
            item = items_by_id.get(m.content_item_id)
            if item:
                content_item_out = schemas.ContentItemOut(
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
        results.append(schemas.ContentMatchOut(
            content_type=m.content_type,
            match_status=m.match_status,
            match_score=m.match_score,
            quality_notes=m.quality_notes,
            matched_concepts=json.loads(m.matched_concepts or "[]"),
            customized_title=m.customized_title,
            customized_description=m.customized_description,
            content_item=content_item_out,
        ))
    return results


@router.get(
    "/",
    response_model=list[schemas.ChapterOut],
    summary="List chapters uploaded by the current user",
)
def list_my_chapters(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(models.Chapter)
    if current_user.role != "admin":
        query = query.filter(models.Chapter.uploaded_by == current_user.id)
    return query.order_by(models.Chapter.created_at.desc()).all()
