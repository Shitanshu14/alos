from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from jose import JWTError

from .. import models, schemas, auth
from ..database import get_db
from ..config import settings
from ..rate_limiter import limiter
from ..email_utils import send_verification_email, send_password_reset_email

router = APIRouter(prefix="/auth", tags=["Authentication"])

# Ye batata hai FastAPI ko ki token kahan se milega (Swagger docs me "Authorize" button ke liye)
security_scheme = HTTPBearer()


# =========================================================
# SIGNUP
# =========================================================
@router.post("/signup", response_model=schemas.SignupResponse, status_code=status.HTTP_201_CREATED)
def signup(user: schemas.UserCreate, db: Session = Depends(get_db)):
    existing_email = db.query(models.User).filter(models.User.email == user.email).first()
    if existing_email:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ye email pehle se registered hai")

    existing_phone = db.query(models.User).filter(models.User.phone == user.phone).first()
    if existing_phone:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ye phone number pehle se registered hai")

    new_user = models.User(
        full_name=user.full_name,
        email=user.email,
        phone=user.phone,
        hashed_password=auth.hash_password(user.password),
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # Email verification token banao aur "verification mail" bhejo
    verify_token = auth.generate_secure_token()
    db.add(models.EmailVerificationToken(
        token=verify_token,
        user_id=new_user.id,
        expires_at=auth.verify_token_expiry(),
    ))
    db.commit()
    send_verification_email(new_user.email, verify_token)

    return {
        "message": "Signup successful. Apna email verify karne ke liye inbox check karo.",
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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email ya password galat hai")

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

    invalid_exc = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token invalid ya expire ho gaya hai")

    if not token_row or token_row.revoked or token_row.expires_at < datetime.utcnow():
        raise invalid_exc

    user = db.query(models.User).filter(models.User.id == token_row.user_id).first()
    if not user:
        raise invalid_exc

    # Rotation: purana refresh token revoke karke naya issue karo (replay attack se bachne ke liye)
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
    # Chahe token mile ya na mile, hamesha success return karo (idempotent + no info leak)
    return {"message": "Logout ho gaya"}


# =========================================================
# FORGOT PASSWORD
# =========================================================
@router.post("/forgot-password", response_model=schemas.MessageResponse)
def forgot_password(payload: schemas.ForgotPasswordRequest, db: Session = Depends(get_db)):
    generic_message = {"message": "Agar ye email registered hai, to reset link bhej diya gaya hai"}

    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if not user:
        # Jaan-boojh kar same response - taaki koi ye pata na laga sake ki
        # kaunse emails registered hain (user enumeration se bachne ke liye)
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

    invalid_exc = HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reset token invalid ya expire ho gaya hai")

    if not token_row or token_row.used or token_row.expires_at < datetime.utcnow():
        raise invalid_exc

    user = db.query(models.User).filter(models.User.id == token_row.user_id).first()
    if not user:
        raise invalid_exc

    user.hashed_password = auth.hash_password(payload.new_password)
    token_row.used = True

    # Password change hone par saare existing refresh tokens revoke kar do (security best practice)
    db.query(models.RefreshToken).filter(
        models.RefreshToken.user_id == user.id, models.RefreshToken.revoked == False  # noqa: E712
    ).update({"revoked": True})

    db.commit()
    return {"message": "Password reset ho gaya. Ab naye password se login karo"}


# =========================================================
# EMAIL VERIFICATION
# =========================================================
@router.post("/verify-email", response_model=schemas.MessageResponse)
def verify_email(payload: schemas.VerifyEmailRequest, db: Session = Depends(get_db)):
    token_row = db.query(models.EmailVerificationToken).filter(
        models.EmailVerificationToken.token == payload.token
    ).first()

    invalid_exc = HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Verification token invalid ya expire ho gaya hai")

    if not token_row or token_row.used or token_row.expires_at < datetime.utcnow():
        raise invalid_exc

    user = db.query(models.User).filter(models.User.id == token_row.user_id).first()
    if not user:
        raise invalid_exc

    user.is_verified = True
    token_row.used = True
    db.commit()

    return {"message": "Email verify ho gaya"}


# =========================================================
# CURRENT USER (existing protected-route dependency)
# =========================================================
def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security_scheme), db: Session = Depends(get_db)) -> models.User:
    """Ye function kisi bhi protected route me use karo taaki pata chale
    abhi kaunsa user login hai (token ke through)."""
    token = credentials.credentials
    credentials_exception = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login invalid ya expire ho gaya hai")
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
    """Login karne ke baad ye endpoint call karke apni profile check kar sakte ho."""
    return current_user
