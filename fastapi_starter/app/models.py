import datetime

from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Table
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

    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)  # null for system-seeded items

    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow, nullable=False,
    )

    tags = relationship("ContentTag", secondary=content_item_tags, back_populates="items")
