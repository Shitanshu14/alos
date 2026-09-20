import re
import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, field_validator, Field

from .config import settings


PASSWORD_REGEX_UPPER = re.compile(r"[A-Z]")
PASSWORD_REGEX_LOWER = re.compile(r"[a-z]")
PASSWORD_REGEX_DIGIT = re.compile(r"\d")
PASSWORD_REGEX_SPECIAL = re.compile(r"[!@#$%^&*()\-_=+\[\]{};:'\",.<>/?\\|`~]")

# Roles that exist in the system:
#   school  - institution-level account (same dashboard view as teacher for now)
#   teacher - uploads chapters, runs classrooms, sees processing status
#   parent  - sees their children's learning progress
#   student - enrolled in classrooms, sees assigned content + progress
#   user    - general-purpose account (no role-specific dashboard yet)
#   admin   - platform-wide oversight (content library, feedback, any chapter)
ALL_ROLES = {"school", "parent", "teacher", "student", "user", "admin"}

# Roles that can be picked at public signup (POST /auth/signup): every role.
# Anyone can therefore register as an admin - that is intentional (see
# ALLOW_ADMIN_SIGNUP in config.py / .env.example). Set ALLOW_ADMIN_SIGNUP=false
# to take "admin" off the public signup list (then create admins with
# `python -m app.create_admin`).
ALLOWED_SIGNUP_ROLES = ALL_ROLES if settings.ALLOW_ADMIN_SIGNUP else ALL_ROLES - {"admin"}


def validate_password_strength(password: str) -> str:
    """Password policy: min 8 characters, at least one uppercase, lowercase,
    number, and one special character."""
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters long")
    # bcrypt (used in auth.py for hashing) only considers the first 72
    # BYTES of a password - anything beyond that is silently ignored, not
    # rejected. Without this check, two different passwords sharing the
    # same first 72 bytes would both successfully log in, silently
    # weakening any password longer than that. Checked in bytes (not
    # characters) since multi-byte UTF-8 characters count as more than
    # one byte against bcrypt's actual limit.
    if len(password.encode("utf-8")) > 72:
        raise ValueError("Password must be at most 72 bytes long (bcrypt's limit) - roughly 72 characters for plain text")
    if not PASSWORD_REGEX_UPPER.search(password):
        raise ValueError("Password must contain at least one uppercase letter")
    if not PASSWORD_REGEX_LOWER.search(password):
        raise ValueError("Password must contain at least one lowercase letter")
    if not PASSWORD_REGEX_DIGIT.search(password):
        raise ValueError("Password must contain at least one number")
    if not PASSWORD_REGEX_SPECIAL.search(password):
        raise ValueError("Password must contain at least one special character (!@#$% etc.)")
    return password


# --- User related schemas ---
def _normalize_email(v: str) -> str:
    """Emails are case-insensitive by spec (the domain part always is; in
    practice virtually every real mail provider treats the local part the
    same way too) - normalize to lowercase everywhere so 'User@Test.com'
    and 'user@test.com' are always treated as the same account."""
    return v.strip().lower()


class UserCreate(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=150)
    email: EmailStr
    phone: str = Field(..., min_length=5, max_length=20)
    password: str = Field(..., max_length=128)  # min length enforced by check_password below
    role: str  # required - must be one of ALLOWED_SIGNUP_ROLES (see the role list above)

    @field_validator("full_name")
    @classmethod
    def check_full_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("full_name cannot be empty or just whitespace")
        return v

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return _normalize_email(v)

    @field_validator("password")
    @classmethod
    def check_password(cls, v: str) -> str:
        return validate_password_strength(v)

    @field_validator("role")
    @classmethod
    def check_role(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ALLOWED_SIGNUP_ROLES:
            raise ValueError(f"Role must be one of: {', '.join(sorted(ALLOWED_SIGNUP_ROLES))}")
        return v


class UserOut(BaseModel):
    id: int
    full_name: str
    email: EmailStr
    phone: str
    role: str
    is_verified: bool
    created_at: datetime.datetime

    class Config:
        from_attributes = True


class SignupResponse(BaseModel):
    message: str
    user: UserOut


class LoginRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return _normalize_email(v)


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return _normalize_email(v)


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def check_password(cls, v: str) -> str:
        return validate_password_strength(v)


class VerifyEmailRequest(BaseModel):
    token: str


class MessageResponse(BaseModel):
    message: str


# --- Item related schemas (sample CRUD) ---
class ItemCreate(BaseModel):
    title: str
    description: Optional[str] = None


class ItemOut(BaseModel):
    id: int
    title: str
    description: Optional[str] = None
    owner_id: int
    created_at: datetime.datetime
    updated_at: datetime.datetime

    class Config:
        from_attributes = True


# --- Chapter related schemas (Week 3: Chapter Upload) ---

ALLOWED_CHAPTER_FILE_TYPES = {"pdf", "docx"}


class ChapterOut(BaseModel):
    id: int
    class_name: str
    subject: str
    chapter_name: str
    original_filename: str
    file_type: str
    file_size_bytes: int
    status: str
    status_message: Optional[str] = None
    created_at: datetime.datetime
    updated_at: datetime.datetime

    class Config:
        from_attributes = True


class ChapterStatusOut(BaseModel):
    id: int
    chapter_name: str
    status: str
    status_message: Optional[str] = None

    class Config:
        from_attributes = True


# --- Dashboard (Week 2: role-based dashboard) ---

class DashboardOut(BaseModel):
    role: str
    message: str
    chapters_by_status: Optional[dict] = None
    recent_chapters: Optional[list[ChapterOut]] = None
    ready_chapter_count: Optional[int] = None


# --- Chapter brief (Week 4: AI understanding of a chapter) ---

class ChapterBriefOut(BaseModel):
    chapter_id: int
    summary: str
    key_concepts: list[str]
    suggested_content_types: list[str]
    created_at: datetime.datetime


# --- Content Library (Week 5) ---

ALLOWED_CONTENT_TYPES = {"video", "story", "poem", "cartoon"}


def _normalize_tags(tags: list[str]) -> list[str]:
    """Lowercase + strip + dedupe, preserving first-seen order."""
    seen = []
    for t in tags:
        clean = t.strip().lower()
        if clean and clean not in seen:
            seen.append(clean)
    return seen


class ContentItemCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content_type: str
    subject: str = Field(..., min_length=1, max_length=100)
    class_name: str = Field(..., min_length=1, max_length=50)
    description: Optional[str] = Field(None, max_length=1000)
    body: Optional[str] = Field(None, max_length=20000)        # story/poem text
    media_url: Optional[str] = Field(None, max_length=1000)   # video/cartoon link
    tags: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("content_type")
    @classmethod
    def check_content_type(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ALLOWED_CONTENT_TYPES:
            raise ValueError(f"content_type must be one of: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}")
        return v

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, v: list[str]) -> list[str]:
        return _normalize_tags(v)


class ContentItemUpdate(BaseModel):
    """All fields optional - PATCH-style partial update."""
    title: Optional[str] = None
    content_type: Optional[str] = None
    subject: Optional[str] = None
    class_name: Optional[str] = None
    description: Optional[str] = None
    body: Optional[str] = None
    media_url: Optional[str] = None
    tags: Optional[list[str]] = None

    @field_validator("content_type")
    @classmethod
    def check_content_type(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().lower()
        if v not in ALLOWED_CONTENT_TYPES:
            raise ValueError(f"content_type must be one of: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}")
        return v

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return v
        return _normalize_tags(v)


class ContentItemOut(BaseModel):
    id: int
    title: str
    content_type: str
    subject: str
    class_name: str
    description: Optional[str] = None
    body: Optional[str] = None
    media_url: Optional[str] = None
    is_seed_placeholder: bool
    is_generated: bool = False
    tags: list[str] = []
    created_at: datetime.datetime
    updated_at: datetime.datetime

    class Config:
        from_attributes = True


# --- Content Matching (Week 6) ---

class ContentMatchOut(BaseModel):
    content_type: str
    match_status: str          # "matched" | "no_match" | "failed_quality"
    match_score: float
    quality_notes: Optional[str] = None
    matched_concepts: list[str] = []
    customized_title: Optional[str] = None
    customized_description: Optional[str] = None
    content_item: Optional[ContentItemOut] = None


# --- Notifications (Week 8) ---

class NotificationOut(BaseModel):
    id: int
    title: str
    message: str
    is_read: bool
    created_at: datetime.datetime

    class Config:
        from_attributes = True


# --- Pipeline status (Week 8) ---

class ChapterPipelineStatusOut(BaseModel):
    chapter_id: int
    chapter_name: str
    stage: str  # "uploaded" | "processing" | "content_ready" | "failed"
    status_message: Optional[str] = None
    matched_count: int = 0
    generated_count: int = 0
    unresolved_count: int = 0


# --- Progress tracking (Week 8 + 9) ---

class ContentAccessOut(BaseModel):
    """Returned when a learner opens a piece of content - the content
    itself plus their current progress on it, for resuming."""
    content: ContentItemOut
    status: str
    progress_percent: int
    last_position_seconds: Optional[int] = None


class ProgressUpdate(BaseModel):
    status: Optional[str] = None            # "in_progress" | "completed"
    progress_percent: Optional[int] = Field(None, ge=0, le=100)
    last_position_seconds: Optional[int] = Field(None, ge=0)


class ProgressOut(BaseModel):
    content_item_id: int
    chapter_id: Optional[int] = None
    status: str
    progress_percent: int
    last_position_seconds: Optional[int] = None
    updated_at: datetime.datetime
    content: Optional[ContentItemOut] = None


class CompletedChapterOut(BaseModel):
    chapter_id: int
    chapter_name: str
    subject: str
    class_name: str
    completed_at: datetime.datetime


# --- Parent-child (Week 10) ---

class ChildCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    class_name: Optional[str] = Field(None, max_length=50)


class ChildClassUpdate(BaseModel):
    class_name: str = Field(..., min_length=1, max_length=50)


class ChildOut(BaseModel):
    id: int
    name: str
    class_name: Optional[str] = None
    created_at: datetime.datetime

    class Config:
        from_attributes = True


class ChildProgressReportOut(BaseModel):
    child: ChildOut
    completed_chapters: list[CompletedChapterOut] = []
    in_progress: list[ProgressOut] = []


# --- Content Sharing (Week 11) ---

class ShareLinkCreate(BaseModel):
    expires_in_days: Optional[int] = Field(None, ge=1, le=365)


class ShareLinkOut(BaseModel):
    token: str
    share_url: str
    content_item_id: int
    expires_at: Optional[datetime.datetime] = None
    created_at: datetime.datetime


class SharedContentView(BaseModel):
    """Public view returned when resolving a share token - deliberately
    limited to what's safe to show without login (no internal IDs)."""
    title: str
    content_type: str
    description: Optional[str] = None
    body: Optional[str] = None
    media_url: Optional[str] = None
    subject: str
    class_name: str


class SendToChildRequest(BaseModel):
    content_item_id: int
    message: Optional[str] = None


class ClassroomShareRequest(BaseModel):
    student_user_ids: list[int]
    message: Optional[str] = None

    @field_validator("student_user_ids")
    @classmethod
    def check_ids(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("student_user_ids must not be empty")
        if len(v) > 200:
            raise ValueError("Too many students in one share (max 200)")
        return v


class SharedContentOut(BaseModel):
    id: int
    content_item_id: int
    content: Optional[ContentItemOut] = None
    message: Optional[str] = None
    created_at: datetime.datetime


# --- Classrooms + Homework (Week 12) ---

class ClassroomCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    subject: str = Field(..., min_length=1, max_length=100)
    class_name: str = Field(..., min_length=1, max_length=50)


class ClassroomOut(BaseModel):
    id: int
    name: str
    subject: str
    class_name: str
    student_count: int = 0
    created_at: datetime.datetime


class EnrollStudentRequest(BaseModel):
    student_user_id: int


class StudentOut(BaseModel):
    id: int
    full_name: str
    email: str


class HomeworkCreate(BaseModel):
    """Exactly one of chapter_id/content_item_id should be set - checked in
    the route (not here) since Pydantic v2 makes single-field validators
    for cross-field checks unnecessarily awkward."""
    title: str = Field(..., min_length=1, max_length=200)
    message: Optional[str] = Field(None, max_length=2000)
    due_date: Optional[datetime.datetime] = None
    chapter_id: Optional[int] = None
    content_item_id: Optional[int] = None

    @field_validator("due_date")
    @classmethod
    def check_due_date_not_in_past(cls, v: Optional[datetime.datetime]) -> Optional[datetime.datetime]:
        if v is not None:
            # Compare using naive UTC "now" since v may or may not carry
            # timezone info depending on what the client sent - strip tz
            # from both sides for a simple, consistent comparison.
            now = datetime.datetime.utcnow()
            compare_v = v.replace(tzinfo=None) if v.tzinfo else v
            if compare_v < now:
                raise ValueError("due_date cannot be in the past - homework would be overdue the moment it's assigned")
        return v


class HomeworkOut(BaseModel):
    id: int
    classroom_id: int
    title: str
    message: Optional[str] = None
    due_date: Optional[datetime.datetime] = None
    chapter_id: Optional[int] = None
    content_item_id: Optional[int] = None
    created_at: datetime.datetime


# --- Games (Week 13) ---

class ConceptMatchGameOut(BaseModel):
    chapter_id: int
    chapter_name: str
    pairs: list  # [{"concept": str, "clue": str}, ...]


class MemoryGameOut(BaseModel):
    chapter_id: int
    chapter_name: str
    cards: list  # [{"card_id": str, "concept": str}, ...] - each concept appears twice, pre-shuffled


# --- Quizzes (Week 14) ---

class QuizQuestionPublic(BaseModel):
    """What a learner sees BEFORE submitting - no correct answer revealed."""
    question_id: int
    question_text: str
    options: list


class QuizOut(BaseModel):
    quiz_id: int
    chapter_id: int
    chapter_name: str
    questions: list  # list[QuizQuestionPublic]


class QuizSubmitRequest(BaseModel):
    answers: list  # list[int], one option index per question, in question order


class QuizResultOut(BaseModel):
    attempt_number: int
    score: int
    total_questions: int
    correct_indices: list  # the right answer for each question, revealed after submitting


class QuizAttemptOut(BaseModel):
    attempt_number: int
    score: int
    total_questions: int
    created_at: datetime.datetime


# --- Feedback (Week 16) ---

class FeedbackCreate(BaseModel):
    category: str = "general"
    message: str = Field(..., min_length=1, max_length=5000)
    rating: Optional[int] = Field(None, ge=1, le=5)

    @field_validator("category")
    @classmethod
    def check_category(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("bug", "feature_request", "general"):
            raise ValueError("category must be one of: bug, feature_request, general")
        return v


class FeedbackOut(BaseModel):
    id: int
    category: str
    message: str
    rating: Optional[int] = None
    status: str
    created_at: datetime.datetime

    class Config:
        from_attributes = True
