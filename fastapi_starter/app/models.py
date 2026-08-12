import datetime

from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Table, Float
from sqlalchemy.orm import relationship
from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    phone = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)
    role = Column(String, nullable=False)  # teacher / parent / student / school / admin
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow, nullable=False,
    )


# Sample table - modify this to fit your app
class Item(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True, nullable=False)
    description = Column(String, nullable=True)
    owner_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow, nullable=False,
    )


class RefreshToken(Base):
    """Record of every issued refresh token - used for logout and token revocation."""
    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String, unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    revoked = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String, unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


class EmailVerificationToken(Base):
    __tablename__ = "email_verification_tokens"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String, unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


class ErrorLog(Base):
    """Every unhandled exception (crash) is logged here —
    the global exception handler (main.py) writes to this automatically."""
    __tablename__ = "error_logs"

    id = Column(Integer, primary_key=True, index=True)
    path = Column(String, nullable=False)          # which endpoint, e.g. /auth/signup
    method = Column(String, nullable=False)         # GET / POST / etc.
    error_type = Column(String, nullable=False)     # exception class ka naam, e.g. ValueError
    error_message = Column(String, nullable=False)  # str(exception)
    traceback = Column(String, nullable=True)        # full traceback (for debugging)
    client_ip = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, index=True, nullable=False)


class Chapter(Base):
    """A school chapter uploaded by a teacher/parent. This is the starting
    point of the whole ALOS content pipeline (Week 3 → Week 8 in the plan):
    upload -> processing -> matching/generation -> ready for the student."""
    __tablename__ = "chapters"

    id = Column(Integer, primary_key=True, index=True)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    class_name = Column(String, nullable=False)      # e.g. "Class 6"
    subject = Column(String, nullable=False)         # e.g. "Science"
    chapter_name = Column(String, nullable=False)    # e.g. "Photosynthesis"

    original_filename = Column(String, nullable=False)
    file_path = Column(String, nullable=False)       # where the file is stored on disk
    file_type = Column(String, nullable=False)       # "pdf" or "docx"
    file_size_bytes = Column(Integer, nullable=False)

    # Pipeline status: uploaded -> processing -> ready -> failed
    status = Column(String, default="uploaded", nullable=False, index=True)
    status_message = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow, nullable=False,
    )


class ChapterBrief(Base):
    """Week 4 output: the AI-generated understanding of a chapter - its
    summary, key concepts, and suggested content types. Produced by the
    background processing pipeline right after upload (see chapters_router.py
    _process_chapter). Week 5/6 (content library + matching) will read this
    to find or generate the right learning content for the chapter."""
    __tablename__ = "chapter_briefs"

    id = Column(Integer, primary_key=True, index=True)
    chapter_id = Column(Integer, ForeignKey("chapters.id"), unique=True, nullable=False, index=True)

    summary = Column(String, nullable=False)
    key_concepts = Column(String, nullable=False)            # JSON-encoded list[str]
    suggested_content_types = Column(String, nullable=True)  # JSON-encoded list[str]

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Week 5 - Content Library
# ---------------------------------------------------------------------------

# Many-to-many link between ContentItem and ContentTag (a content item can
# cover several concepts, and one concept can appear in many content items -
# this is what Week 6's matching step will query against).
content_item_tags = Table(
    "content_item_tags",
    Base.metadata,
    Column("content_item_id", Integer, ForeignKey("content_items.id"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("content_tags.id"), primary_key=True),
)


class ContentTag(Base):
    """A single concept/topic tag (e.g. 'photosynthesis', 'fractions').
    Normalized (lowercase, deduplicated) so matching (Week 6) can compare
    a chapter's key_concepts against these directly."""
    __tablename__ = "content_tags"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)

    items = relationship("ContentItem", secondary=content_item_tags, back_populates="tags")


class ContentItem(Base):
    """One piece of ready-made learning content in the library - a video,
    story, poem, or cartoon. Seeded in bulk (see seed_content_library.py)
    and manageable via the admin endpoints (content_library_router.py).
    Week 6 (matching) will search this table by subject/class_name/tags to
    find the best existing item for a chapter before generating anything new."""
    __tablename__ = "content_items"

    id = Column(Integer, primary_key=True, index=True)

    title = Column(String, nullable=False)
    content_type = Column(String, nullable=False, index=True)  # "video" | "story" | "poem" | "cartoon"
    description = Column(String, nullable=True)                # short one-line blurb

    # Direct filter columns (fast lookups without joining tags) -
    # every item belongs to one subject and one class level.
    subject = Column(String, nullable=False, index=True)       # e.g. "Science"
    class_name = Column(String, nullable=False, index=True)    # e.g. "Class 6"

    # Actual content: text-based items (story/poem) store their text in
    # `body`; media items (video/cartoon) store a link in `media_url`.
    body = Column(Text, nullable=True)
    media_url = Column(String, nullable=True)

    # True for content inserted by the bulk seed script (placeholder/template
    # text) rather than a real curated item - lets admins find and replace
    # these before pilot launch.
    is_seed_placeholder = Column(Boolean, default=False, nullable=False)

    # True for content auto-generated by the Week 7 pipeline for a specific
    # chapter that had no good library match - lets admins find these and
    # review/replace with real produced content (especially video/cartoon,
    # which are only a text script until someone actually produces them).
    is_generated = Column(Boolean, default=False, nullable=False)
    generated_for_chapter_id = Column(Integer, ForeignKey("chapters.id"), nullable=True)

    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)  # null for system-seeded items

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow, nullable=False,
    )

    tags = relationship("ContentTag", secondary=content_item_tags, back_populates="items")


class ContentMatch(Base):
    """Week 6 output: for each content type (video/story/poem/cartoon),
    the best existing content library item found for a chapter - or none,
    if nothing met the quality bar (Week 7's generation step picks up
    from there). One row per (chapter, content_type)."""
    __tablename__ = "content_matches"

    id = Column(Integer, primary_key=True, index=True)
    chapter_id = Column(Integer, ForeignKey("chapters.id"), nullable=False, index=True)
    content_type = Column(String, nullable=False)  # video / story / poem / cartoon

    content_item_id = Column(Integer, ForeignKey("content_items.id"), nullable=True)  # null = no match found
    match_score = Column(Float, nullable=False, default=0.0)
    match_status = Column(String, nullable=False)  # "matched" | "no_match" | "failed_quality"
    quality_notes = Column(String, nullable=True)

    matched_concepts = Column(String, nullable=True)          # JSON-encoded list[str]
    customized_title = Column(String, nullable=True)
    customized_description = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    content_item = relationship("ContentItem")


# ---------------------------------------------------------------------------
# Week 8 - Notifications
# ---------------------------------------------------------------------------

class Notification(Base):
    """In-app notification for a user (e.g. 'Your chapter is ready').
    Email is sent alongside this via services/notification_service.py when
    requested - this row is what powers the in-app notification bell/list."""
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    title = Column(String, nullable=False)
    message = Column(String, nullable=False)
    is_read = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Week 8 + 9 - Progress tracking
# ---------------------------------------------------------------------------

class ContentProgress(Base):
    """Tracks how far a learner has gotten through one content item, so
    they can resume where they left off (Week 8) and so we can compute
    completed-chapters / continue-learning / history views (Week 9).

    owner_type/owner_id is used instead of a plain user_id foreign key
    because a "learner" here can be either a logged-in User (teacher,
    student, school account) OR a Child profile managed by a parent
    (Week 10) - Child rows aren't full login accounts."""
    __tablename__ = "content_progress"

    id = Column(Integer, primary_key=True, index=True)

    owner_type = Column(String, nullable=False, index=True)  # "user" | "child"
    owner_id = Column(Integer, nullable=False, index=True)

    content_item_id = Column(Integer, ForeignKey("content_items.id"), nullable=False, index=True)
    chapter_id = Column(Integer, ForeignKey("chapters.id"), nullable=True, index=True)

    status = Column(String, nullable=False, default="not_started")  # not_started | in_progress | completed
    progress_percent = Column(Integer, nullable=False, default=0)   # 0-100
    last_position_seconds = Column(Integer, nullable=True)          # for resuming video playback

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow, nullable=False,
    )


# ---------------------------------------------------------------------------
# Week 10 - Parent-child
# ---------------------------------------------------------------------------

class Child(Base):
    """A child profile managed by a parent account. Children don't get
    their own login in this phase of ALOS - the parent adds/manages them
    and views their learning progress (see routers/children_router.py)."""
    __tablename__ = "children"

    id = Column(Integer, primary_key=True, index=True)
    parent_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    name = Column(String, nullable=False)
    class_name = Column(String, nullable=True)  # e.g. "Class 4" - set via the class-selection endpoint

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Week 11 - Content Sharing
# ---------------------------------------------------------------------------

class ShareLink(Base):
    """Week 11 — a public, no-login-required link to view one content item
    (typically a story or video). Token is random/unguessable; optional
    expiry. view_count is a simple usage signal."""
    __tablename__ = "share_links"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String, unique=True, index=True, nullable=False)
    content_item_id = Column(Integer, ForeignKey("content_items.id"), nullable=False, index=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)

    expires_at = Column(DateTime, nullable=True)
    view_count = Column(Integer, default=0, nullable=False)

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


class SharedContent(Base):
    """Week 11 — records content deliberately shared with a specific
    target: either a Child profile (parent -> child, 'send to child') or
    a student User account (teacher/school -> student, 'classroom
    sharing' - broadcasting to a list of students, since a formal
    Classroom entity doesn't exist until Week 12)."""
    __tablename__ = "shared_content"

    id = Column(Integer, primary_key=True, index=True)
    content_item_id = Column(Integer, ForeignKey("content_items.id"), nullable=False, index=True)
    shared_by = Column(Integer, ForeignKey("users.id"), nullable=False)

    target_type = Column(String, nullable=False)  # "child" | "user"
    target_id = Column(Integer, nullable=False, index=True)

    message = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Week 12 - Classrooms + homework
# ---------------------------------------------------------------------------

class Classroom(Base):
    """A teacher/school's classroom - a group of enrolled students who get
    automatic access to any content the teacher assigns as homework
    (see Homework below and 'auto student access' in classrooms_router.py)."""
    __tablename__ = "classrooms"

    id = Column(Integer, primary_key=True, index=True)
    teacher_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    name = Column(String, nullable=False)          # e.g. "Section A"
    subject = Column(String, nullable=False)
    class_name = Column(String, nullable=False)     # e.g. "Class 6"

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


class ClassroomEnrollment(Base):
    """One student enrolled in one classroom."""
    __tablename__ = "classroom_enrollments"

    id = Column(Integer, primary_key=True, index=True)
    classroom_id = Column(Integer, ForeignKey("classrooms.id"), nullable=False, index=True)
    student_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    enrolled_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


class Homework(Base):
    """A chapter (or a specific content item) assigned to a classroom.
    The moment this is created, every enrolled student automatically has
    access to it - no per-student sharing needed (see
    classrooms_router.py's /content endpoint)."""
    __tablename__ = "homework"

    id = Column(Integer, primary_key=True, index=True)
    classroom_id = Column(Integer, ForeignKey("classrooms.id"), nullable=False, index=True)
    assigned_by = Column(Integer, ForeignKey("users.id"), nullable=False)

    chapter_id = Column(Integer, ForeignKey("chapters.id"), nullable=True)
    content_item_id = Column(Integer, ForeignKey("content_items.id"), nullable=True)

    title = Column(String, nullable=False)
    message = Column(String, nullable=True)
    due_date = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Week 13 - Games
# ---------------------------------------------------------------------------

class GameTemplate(Base):
    """A generated game's data for one chapter, cached so it's not
    regenerated on every request. game_type is 'concept_match' or
    'memory' (see services/game_generation_service.py)."""
    __tablename__ = "game_templates"

    id = Column(Integer, primary_key=True, index=True)
    chapter_id = Column(Integer, ForeignKey("chapters.id"), nullable=False, index=True)
    game_type = Column(String, nullable=False, index=True)

    data = Column(Text, nullable=False)  # JSON-encoded game payload

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Week 14 - Quizzes
# ---------------------------------------------------------------------------

class Quiz(Base):
    """One auto-generated quiz per chapter (5 questions - see
    services/quiz_generation_service.py). Generated once, then reused for
    every student/attempt (retries don't regenerate it)."""
    __tablename__ = "quizzes"

    id = Column(Integer, primary_key=True, index=True)
    chapter_id = Column(Integer, ForeignKey("chapters.id"), unique=True, nullable=False, index=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


class QuizQuestion(Base):
    """One multiple-choice question belonging to a Quiz."""
    __tablename__ = "quiz_questions"

    id = Column(Integer, primary_key=True, index=True)
    quiz_id = Column(Integer, ForeignKey("quizzes.id"), nullable=False, index=True)

    question_text = Column(String, nullable=False)
    options = Column(Text, nullable=False)      # JSON-encoded list[str], 4 options
    correct_index = Column(Integer, nullable=False)  # index into options

    order = Column(Integer, nullable=False, default=0)


class QuizAttempt(Base):
    """One learner's attempt at a quiz. Unlimited retries are allowed -
    attempt_number just increments each time (see quizzes_router.py)."""
    __tablename__ = "quiz_attempts"

    id = Column(Integer, primary_key=True, index=True)
    quiz_id = Column(Integer, ForeignKey("quizzes.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    attempt_number = Column(Integer, nullable=False)
    score = Column(Integer, nullable=False)
    total_questions = Column(Integer, nullable=False)
    answers = Column(Text, nullable=False)  # JSON-encoded list[int], the learner's chosen option indices

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Week 16 - Feedback intake
# ---------------------------------------------------------------------------

class Feedback(Base):
    """Free-form feedback from any logged-in user during the pilot -
    bug reports, feature requests, general comments. Reviewed by admins
    via GET /feedback (feedback_router.py)."""
    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    category = Column(String, nullable=False, default="general")  # bug | feature_request | general
    message = Column(String, nullable=False)
    rating = Column(Integer, nullable=True)  # optional 1-5

    status = Column(String, nullable=False, default="open")  # open | reviewed | resolved

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
