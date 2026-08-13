import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError, OperationalError
from jose import JWTError

from .. import models, schemas, auth
from ..database import get_db
from ..config import settings
from ..rate_limiter import limiter
from ..email_utils import send_verification_email, send_password_reset_email

logger = logging.getLogger("app.auth")

router = APIRouter(prefix="/auth", tags=["Authentication"])

# Tells FastAPI where to get the token from (for the "Authorize" button in Swagger docs)
security_scheme = HTTPBearer()


# =========================================================
# SIGNUP
# =========================================================
@router.post("/signup", response_model=schemas.SignupResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(settings.SIGNUP_RATE_LIMIT)
def signup(request: Request, user: schemas.UserCreate, db: Session = Depends(get_db)):
    existing_email = db.query(models.User).filter(models.User.email == user.email).first()
    if existing_email:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This email is already registered")

    existing_phone = db.query(models.User).filter(models.User.phone == user.phone).first()
    if existing_phone:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This phone number is already registered")

    new_user = models.User(
        full_name=user.full_name,
        email=user.email,
        phone=user.phone,
        hashed_password=auth.hash_password(user.password),
        role=user.role,
    )
    db.add(new_user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This email or phone number is already registered",
        )
    except OperationalError:
        db.rollback()
        logger.exception("Database was unreachable during signup")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is temporarily unavailable. Please try again in a moment.",
        )
    db.refresh(new_user)

    try:
        verify_token = auth.generate_secure_token()
        db.add(models.EmailVerificationToken(
            token=verify_token,
            user_id=new_user.id,
            expires_at=auth.verify_token_expiry(),
        ))
        db.commit()
        email_sent = send_verification_email(new_user.email, verify_token)
    except Exception:
        db.rollback()
        logger.exception("Failed to create/send email verification token for user_id=%s", new_user.id)
        email_sent = False

    if email_sent:
        message = "Signup successful. Please check your inbox to verify your email."
    else:
        # SMTP isn't configured, so no email actually went out — don't claim it did.
        message = "Signup successful. Email sending isn't configured yet, so no verification email was sent."

    return {
        "message": message,
        "user": new_user,
    }


# =========================================================
# LOGIN
# =========================================================
@router.post("/login", response_model=schemas.Token)
@limiter.limit(settings.LOGIN_RATE_LIMIT)
def login(request: Request, login_data: schemas.LoginRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == login_data.email).first()
    if not user or not auth.verify_password(login_data.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")

    return _issue_tokens(user, db)


def _issue_tokens(user: models.User, db: Session) -> dict:
    access_token = auth.create_access_token(data={"sub": str(user.id)})

    refresh_token_str = auth.generate_refresh_token()
    db.add(models.RefreshToken(
        token=refresh_token_str,
        user_id=user.id,
        expires_at=auth.refresh_token_expiry(),
    ))
    db.commit()

    return {
        "access_token": access_token,
        "refresh_token": refresh_token_str,
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


# =========================================================
# REFRESH TOKEN
# =========================================================
@router.post("/refresh", response_model=schemas.Token)
def refresh_token(payload: schemas.RefreshRequest, db: Session = Depends(get_db)):
    token_row = db.query(models.RefreshToken).filter(
        models.RefreshToken.token == payload.refresh_token
    ).first()

    invalid_exc = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token is invalid or has expired")

    if not token_row or token_row.revoked or token_row.expires_at < datetime.utcnow():
        raise invalid_exc

    user = db.query(models.User).filter(models.User.id == token_row.user_id).first()
    if not user:
        raise invalid_exc

    # Rotation: revoke the old refresh token and issue a new one (protects against replay attacks)
    token_row.revoked = True
    db.commit()

    return _issue_tokens(user, db)


# =========================================================
# LOGOUT
# =========================================================
@router.post("/logout", response_model=schemas.MessageResponse)
def logout(payload: schemas.LogoutRequest, db: Session = Depends(get_db)):
    token_row = db.query(models.RefreshToken).filter(
        models.RefreshToken.token == payload.refresh_token
    ).first()
    if token_row:
        token_row.revoked = True
        db.commit()
    # Always return success whether or not the token was found (idempotent + no info leak)
    return {"message": "Logged out successfully"}


# =========================================================
# FORGOT PASSWORD
# =========================================================
@router.post("/forgot-password", response_model=schemas.MessageResponse)
@limiter.limit(settings.FORGOT_PASSWORD_RATE_LIMIT)
def forgot_password(request: Request, payload: schemas.ForgotPasswordRequest, db: Session = Depends(get_db)):
    # Whether SMTP is configured is a global setting (not per-user), so checking it
    # here doesn't leak whether this particular email is registered.
    if settings.SMTP_HOST:
        generic_message = {"message": "If this email is registered, a reset link has been sent"}
    else:
        generic_message = {"message": "Email sending isn't configured yet, so no reset link was sent"}

    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if not user:
        # Deliberately return the same response either way - so no one can tell
        # which emails are registered (avoids user enumeration).
        return generic_message

    reset_token = auth.generate_secure_token()
    db.add(models.PasswordResetToken(
        token=reset_token,
        user_id=user.id,
        expires_at=auth.reset_token_expiry(),
    ))
    db.commit()
    send_password_reset_email(user.email, reset_token)

    return generic_message


# =========================================================
# RESET PASSWORD
# =========================================================
@router.post("/reset-password", response_model=schemas.MessageResponse)
def reset_password(payload: schemas.ResetPasswordRequest, db: Session = Depends(get_db)):
    token_row = db.query(models.PasswordResetToken).filter(
        models.PasswordResetToken.token == payload.token
    ).first()

    invalid_exc = HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reset token is invalid or has expired")

    if not token_row or token_row.used or token_row.expires_at < datetime.utcnow():
        raise invalid_exc

    user = db.query(models.User).filter(models.User.id == token_row.user_id).first()
    if not user:
        raise invalid_exc

    user.hashed_password = auth.hash_password(payload.new_password)
    token_row.used = True

    # Revoke all existing refresh tokens when the password changes (security best practice)
    db.query(models.RefreshToken).filter(
        models.RefreshToken.user_id == user.id, models.RefreshToken.revoked == False  # noqa: E712
    ).update({"revoked": True})

    db.commit()
    return {"message": "Password reset successful. Please log in with your new password"}


# =========================================================
# EMAIL VERIFICATION
# =========================================================
@router.post("/verify-email", response_model=schemas.MessageResponse)
def verify_email(payload: schemas.VerifyEmailRequest, db: Session = Depends(get_db)):
    token_row = db.query(models.EmailVerificationToken).filter(
        models.EmailVerificationToken.token == payload.token
    ).first()

    invalid_exc = HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Verification token is invalid or has expired")

    if not token_row or token_row.used or token_row.expires_at < datetime.utcnow():
        raise invalid_exc

    user = db.query(models.User).filter(models.User.id == token_row.user_id).first()
    if not user:
        raise invalid_exc

    user.is_verified = True
    token_row.used = True
    db.commit()

    return {"message": "Email verified successfully"}


# =========================================================
# CURRENT USER (existing protected-route dependency)
# =========================================================
def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security_scheme), db: Session = Depends(get_db)) -> models.User:
    """Use this function in any protected route to determine which
    user is currently logged in (via the token)."""
    token = credentials.credentials
    credentials_exception = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login is invalid or has expired")
    try:
        payload = auth.decode_access_token(token)
        if payload.get("type") != "access":
            raise credentials_exception
        user_id = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(models.User).filter(models.User.id == int(user_id)).first()
    if user is None:
        raise credentials_exception
    return user


@router.get("/me", response_model=schemas.UserOut)
def read_own_profile(current_user: models.User = Depends(get_current_user)):
    """After logging in, call this endpoint to check your own profile."""
    return current_user


def require_admin(current_user: models.User = Depends(get_current_user)) -> models.User:
    """Use this in any route that only admins should be able to call
    (e.g. content library management)."""
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user
