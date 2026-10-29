"""
Week 3 — Chapter Upload.
Week 4 — Chapter processing (text extraction -> AI understanding -> content brief).

Lets any logged-in user upload a school chapter (PDF or DOCX)
along with its class and subject. The file is saved to disk and a
Chapter row is created with status="uploaded". Right after that, a
background task picks the chapter up and moves it through
"processing" -> "ready" (or "failed"), producing a ChapterBrief along
the way (summary + key concepts + suggested content types) that Week
5/6 (content library + matching) will use next.
"""
import io
import json
import logging
import os
import threading
import uuid
import zipfile
import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, UploadFile, File, Form, status
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
from ..services.video_rendering_service import render_video, narration_available, delete_rendered, VideoRenderingError
from ..services.notification_service import notify
from ..services.access_control_service import can_access_chapter
from ..rate_limiter import limiter, user_or_ip_key
from .auth_router import get_current_user

logger = logging.getLogger("app.chapters")

router = APIRouter(prefix="/chapters", tags=["Chapters"])


def _looks_like(ext: str, data: bytes) -> bool:
    """Magic-byte check: a PDF starts with '%PDF-'; a DOCX is a ZIP that contains
    word/document.xml. Stops e.g. an .exe or HTML file renamed to chapter.pdf
    from reaching the PDF/DOCX parsers."""
    if ext == "pdf":
        return data[:1024].lstrip().startswith(b"%PDF-")
    if ext == "docx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                return "word/document.xml" in z.namelist()
        except zipfile.BadZipFile:
            return False
    return False


def _ensure_upload_dir() -> Path:
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def _render_video_into(db: Session, chapter: models.Chapter, generated: dict, gen_reason: str, plan: dict) -> str:
    """Renders the generated video script into a real .mp4 and updates
    `generated` in place. Returns the (possibly extended) quality note.

    The slow part of the whole pipeline (tens of seconds). The DB transaction
    is released first: holding it open for the whole render blocked every other
    writer on SQLite ("database is locked" 500s once a render outlasted the 15 s
    lock timeout) and tied up a Postgres connection."""
    chapter_name = chapter.chapter_name
    db.rollback()  # nothing pending here - this just releases the read transaction/connection
    try:
        relative_path = render_video(chapter_name, generated["body"], settings.GENERATED_MEDIA_DIR)
    except VideoRenderingError as exc:
        logger.warning("Video rendering failed for chapter %s, falling back to script only: %s", chapter.id, exc)
        return gen_reason + f" | Video rendering unavailable, kept as script only ({exc})."

    plan["rendered"].append(relative_path)  # remembered so it can be deleted if the DB write fails
    narrated = narration_available()
    generated["media_url"] = f"/media/{relative_path}"
    generated["title"] = generated["title"].replace(" (Script)", "")
    generated["description"] = (
        f"An auto-generated {'narrated ' if narrated else ''}lesson video on {chapter_name}, "
        "built from the chapter's own text."
    )
    generated["body"] = generated["body"].replace(
        "[Auto-generated narrated lesson built from the chapter's own text. Review it before sharing with students.]",
        "[This script was rendered into the video above (narrated slideshow, auto-generated locally). "
        "Review it before sharing with students.]",
    )
    return gen_reason + (
        " | Rendered as a real video (narrated slideshow)." if narrated
        else " | Rendered as a real video, but SILENT - install espeak-ng for narration."
    )


def _plan_matching(db: Session, chapter: models.Chapter, key_concepts: list, analysis: dict, plan: dict = None) -> dict:
    """Week 6+7, step 1 of 2 - decide what every content type will be, doing
    all the SLOW work (video rendering) without writing to the database:
      1. Find the best library match for each content type (Week 6).
      2. Quality-check each match; detect types with no valid match (Week 7).
      3. For those, generate new content locally - and for video, render the
         real .mp4.
    Returns a plain-data plan consumed by _persist_matching. Files the plan
    created are listed in plan["rendered"] so the caller can delete them if
    persisting fails. The caller may pass its own `plan` dict so that even if
    this function raises half-way, it still knows which files to clean up."""
    plan = plan if plan is not None else {"types": {}, "rendered": []}

    evaluated = evaluate_matches(find_best_matches(db, chapter, key_concepts))
    priority_order = decide_content_types(chapter, analysis.get("suggested_content_types", []))
    logger.info("Chapter %s content type priority: %s", chapter.id, priority_order)

    for content_type in CONTENT_TYPES:
        result = evaluated[content_type]

        if result["is_valid"]:
            custom = customize_for_chapter(result["item"], chapter, result["matched_concepts"])
            plan["types"][content_type] = {
                "kind": "matched", "item_id": result["item"].id, "score": result["score"],
                "reason": result["reason"], "matched_concepts": result["matched_concepts"], "custom": custom,
            }
            continue

        # --- Week 7: no match found - generate new content instead ---
        generated = generate_content(chapter, content_type, key_concepts, analysis)
        is_valid, gen_reason = validate_generated_content(generated)
        if not is_valid:
            plan["types"][content_type] = {
                "kind": "failed_quality", "score": result["score"],
                "reason": f"Library: {result['reason']} | Generation: {gen_reason}",
                "matched_concepts": result["matched_concepts"],
            }
            continue

        if content_type == "video":
            gen_reason = _render_video_into(db, chapter, generated, gen_reason, plan)

        plan["types"][content_type] = {"kind": "generated", "generated": generated, "reason": gen_reason}

    return plan


def _persist_matching(db: Session, chapter: models.Chapter, key_concepts: list, plan: dict) -> None:
    """Week 6+7, step 2 of 2 - write the plan to the database (fast; runs in
    the same transaction as the rest of _process_chapter)."""
    # Clear any previous matches (e.g. if this chapter is reprocessed)
    db.query(models.ContentMatch).filter(models.ContentMatch.chapter_id == chapter.id).delete()

    for content_type in CONTENT_TYPES:
        entry = plan["types"][content_type]

        if entry["kind"] == "matched":
            db.add(models.ContentMatch(
                chapter_id=chapter.id,
                content_type=content_type,
                content_item_id=entry["item_id"],
                match_score=entry["score"],
                match_status="matched",
                quality_notes=entry["reason"],
                matched_concepts=json.dumps(entry["matched_concepts"]),
                customized_title=entry["custom"]["title"],
                customized_description=entry["custom"]["description"],
            ))
            continue

        if entry["kind"] == "failed_quality":
            db.add(models.ContentMatch(
                chapter_id=chapter.id,
                content_type=content_type,
                content_item_id=None,
                match_score=entry["score"],
                match_status="failed_quality",
                quality_notes=entry["reason"],
                matched_concepts=json.dumps(entry["matched_concepts"]),
            ))
            continue

        generated = entry["generated"]
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
            quality_notes=entry["reason"],
            matched_concepts=json.dumps(key_concepts),
            customized_title=generated["title"],
            customized_description=generated["description"],
        ))

    db.flush()  # part of the same transaction as the rest of _process_chapter


def _discard_rendered(plan) -> None:
    """Delete video files a failed pipeline run produced but never saved."""
    for rel in (plan or {}).get("rendered", []):
        delete_rendered(rel, settings.GENERATED_MEDIA_DIR)


def _process_chapter(chapter_id: int) -> None:
    """Week 4 pipeline, run as a background task right after upload:
    extract text -> ask the AI to understand it -> save a ChapterBrief.
    Uses its own DB session since it runs outside the request lifecycle."""
    db = SessionLocal()
    plan = {"types": {}, "rendered": []}   # filled by _plan_matching; also tells the error path what to clean up
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

        # --- Week 6+7 (slow part): match against the library, generate what's
        # missing and render the video - BEFORE any database write, so no
        # transaction is held open while ffmpeg runs (see _render_video_into) ---
        _plan_matching(db, chapter, analysis["key_concepts"], analysis, plan)
        db.rollback()  # release the read transaction before the write phase

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

        # --- Week 6+7 (fast part): save the matches / generated items ---
        _persist_matching(db, chapter, analysis["key_concepts"], plan)

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
        _discard_rendered(plan)   # don't leave an unreferenced .mp4 on disk
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
@limiter.limit(settings.CHAPTER_UPLOAD_RATE_LIMIT, key_func=user_or_ip_key)
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
    # Any logged-in user (teacher, parent, school, student, user, admin) can
    # upload - there is deliberately no role check here. Login is still
    # required (get_current_user above): the upload triggers a heavy
    # background pipeline (text extraction, matching, video rendering), and
    # the Chapter row needs an owner. The per-user rate limit on this
    # endpoint is what caps abuse.
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

    # Guard against accidental double-submission (e.g. double-clicking
    # "Upload" on a slow connection, or a retried request) - if this
    # teacher just uploaded a chapter with the exact same name/subject/
    # class within the last minute, treat it as a duplicate rather than
    # silently creating a second, confusing entry (and wasting a second
    # full background pipeline run, including possible video generation).
    recent_cutoff = datetime.datetime.utcnow() - datetime.timedelta(minutes=1)
    possible_duplicate = (
        db.query(models.Chapter)
        .filter(
            models.Chapter.uploaded_by == current_user.id,
            models.Chapter.chapter_name == chapter_name,
            models.Chapter.subject == subject,
            models.Chapter.class_name == class_name,
            models.Chapter.created_at >= recent_cutoff,
        )
        .first()
    )
    if possible_duplicate:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"You just uploaded a chapter named '{chapter_name}' for {subject} {class_name} "
                f"less than a minute ago (chapter id {possible_duplicate.id}) - if this wasn't "
                "an accidental double-submission, wait a minute and try again."
            ),
        )

    # --- Validate file type ---
    original_filename = file.filename or "chapter"
    ext = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else ""
    if ext not in schemas.ALLOWED_CHAPTER_FILE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Only these file types are allowed: {', '.join(sorted(schemas.ALLOWED_CHAPTER_FILE_TYPES))}",
        )

    # --- Read the file (in chunks, stopping at the limit) and validate size ---
    # A single `await file.read()` pulled the WHOLE upload into memory before
    # the size check, so one huge request could exhaust RAM.
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    buffer = bytearray()
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        buffer += chunk
        if len(buffer) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File is too large. Maximum allowed size is {settings.MAX_UPLOAD_SIZE_MB} MB",
            )
    contents = bytes(buffer)
    if len(contents) == 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Uploaded file is empty")

    # --- Check the CONTENT matches the claimed type (not just the extension) ---
    if not _looks_like(ext, contents):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"The file does not look like a real .{ext} file (its contents don't match its extension)",
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
    limit: int = Query(50, ge=1, le=200, description="Page size (max 200)"),
    offset: int = Query(0, ge=0, description="How many chapters to skip"),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(models.Chapter)
    if current_user.role != "admin":
        # Admins get platform-wide oversight (every chapter); everyone
        # else only sees what they personally uploaded.
        q = q.filter(models.Chapter.uploaded_by == current_user.id)
    return q.order_by(models.Chapter.created_at.desc(), models.Chapter.id.desc()).offset(offset).limit(limit).all()


@router.post(
    "/{chapter_id}/reprocess",
    response_model=schemas.ChapterOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-run the pipeline for a failed (or stuck) chapter",
)
def reprocess_chapter(
    chapter_id: int,
    background_tasks: BackgroundTasks,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Before this endpoint a failed chapter was a dead end: the only option
    was to upload the same file again. Allowed for the uploader or an admin,
    and only when the chapter is 'failed', still just 'uploaded', or has been
    'processing' for longer than STUCK_CHAPTER_MINUTES (e.g. the server restarted)."""
    chapter = db.query(models.Chapter).filter(models.Chapter.id == chapter_id).first()
    if not chapter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chapter not found")
    if chapter.uploaded_by != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the uploader or an admin can reprocess a chapter")

    stale_cutoff = datetime.datetime.utcnow() - datetime.timedelta(minutes=settings.STUCK_CHAPTER_MINUTES)
    is_stuck = chapter.status == "processing" and chapter.updated_at <= stale_cutoff
    if chapter.status not in ("failed", "uploaded") and not is_stuck:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Chapter is '{chapter.status}' - only failed, not-yet-started or stuck chapters can be reprocessed",
        )
    if not Path(chapter.file_path).exists():
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="The original file is no longer on the server (storage was reset) - please upload the chapter again",
        )

    chapter.status = "uploaded"
    chapter.status_message = "Queued for reprocessing"
    db.commit()
    db.refresh(chapter)
    background_tasks.add_task(_process_chapter, chapter.id)
    return chapter


def recover_stuck_chapters() -> int:
    """Called once at startup. Background tasks live in this process's memory,
    so a restart/redeploy mid-pipeline used to leave chapters 'processing' (or
    'uploaded') forever. Re-queues those older than STUCK_CHAPTER_MINUTES, one
    at a time on a single daemon thread (so a backlog can't start a burst of
    simultaneous renders). Returns how many were queued."""
    db = SessionLocal()
    try:
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(minutes=settings.STUCK_CHAPTER_MINUTES)
        ids = [
            c.id for c in db.query(models.Chapter)
            .filter(models.Chapter.status.in_(["processing", "uploaded"]), models.Chapter.updated_at <= cutoff)
            .order_by(models.Chapter.id).all()
            if Path(c.file_path).exists()
        ]
    finally:
        db.close()

    if ids:
        logger.warning("Re-queueing %d chapter(s) left unfinished by a restart: %s", len(ids), ids)

        def _worker():
            for cid in ids:
                _process_chapter(cid)

        threading.Thread(target=_worker, name="chapter-recovery", daemon=True).start()
    return len(ids)
