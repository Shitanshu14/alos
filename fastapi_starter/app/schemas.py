import re
import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, field_validator


PASSWORD_REGEX_UPPER = re.compile(r"[A-Z]")
PASSWORD_REGEX_LOWER = re.compile(r"[a-z]")
PASSWORD_REGEX_DIGIT = re.compile(r"\d")
PASSWORD_REGEX_SPECIAL = re.compile(r"[!@#$%^&*()\-_=+\[\]{};:'\",.<>/?\\|`~]")

# Registration ke waqt sirf ye roles allow honge (security: koi bhi khud ko
# "admin" bana ke register na kar sake, isliye whitelist rakhi hai)
ALLOWED_ROLES = {"user", "admin", "manager"}


def validate_password_strength(password: str) -> str:
    """Password policy: min 8 chars, kam se kam ek uppercase, lowercase,
    number, aur ek special character."""
    if len(password) < 8:
        raise ValueError("Password kam se kam 8 characters ka hona chahiye")
    if not PASSWORD_REGEX_UPPER.search(password):
        raise ValueError("Password me kam se kam ek uppercase letter hona chahiye")
    if not PASSWORD_REGEX_LOWER.search(password):
        raise ValueError("Password me kam se kam ek lowercase letter hona chahiye")
    if not PASSWORD_REGEX_DIGIT.search(password):
        raise ValueError("Password me kam se kam ek number hona chahiye")
    if not PASSWORD_REGEX_SPECIAL.search(password):
        raise ValueError("Password me kam se kam ek special character hona chahiye (!@#$% waghera)")
    return password


# --- User related schemas ---
class UserCreate(BaseModel):
    full_name: str
    email: EmailStr
    phone: str
    password: str
    role: str = "user"

    @field_validator("password")
    @classmethod
    def check_password(cls, v: str) -> str:
        return validate_password_strength(v)

    @field_validator("role")
    @classmethod
    def check_role(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ALLOWED_ROLES:
            raise ValueError(f"Role sirf inme se ek hona chahiye: {', '.join(sorted(ALLOWED_ROLES))}")
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
