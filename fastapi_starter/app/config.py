"""
All settings are loaded from here — from environment variables.
For production, create a .env file (by copying .env.example) and
put the real values there. Never hardcode secrets (SECRET_KEY, SMTP
password, etc.) in the code.
"""
import os
from dotenv import load_dotenv

load_dotenv()  # Loads the .env file if it exists


def _get_bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _get_list(key: str, default: list) -> list:
    val = os.getenv(key)
    if val is None:
        return default
    return [item.strip() for item in val.split(",") if item.strip()]


class Settings:
    # --- JWT / Auth ---
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-this-secret-key-in-production")
    ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

    # --- Password reset / email verification token expiry ---
    RESET_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("RESET_TOKEN_EXPIRE_MINUTES", "30"))
    VERIFY_TOKEN_EXPIRE_HOURS: int = int(os.getenv("VERIFY_TOKEN_EXPIRE_HOURS", "24"))

    # --- Database ---
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./app.db")

    # --- CORS ---
    # Never use "*" in production — put your actual frontend domain here.
    # Comma separated: CORS_ORIGINS=https://myapp.com,https://www.myapp.com
    CORS_ORIGINS: list = _get_list("CORS_ORIGINS", ["http://localhost:3000", "http://127.0.0.1:3000"])

    # --- HTTPS redirect (set to True in production, unless the proxy/host already enforces HTTPS) ---
    FORCE_HTTPS: bool = _get_bool("FORCE_HTTPS", False)

    # --- Rate limiting ---
    LOGIN_RATE_LIMIT: str = os.getenv("LOGIN_RATE_LIMIT", "5/minute")

    # --- Email / SMTP ---
    # If this isn't configured, emails will be printed to the console/log instead (dev mode).
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM_EMAIL: str = os.getenv("SMTP_FROM_EMAIL", "no-reply@example.com")
    SMTP_USE_TLS: bool = _get_bool("SMTP_USE_TLS", True)

    # Frontend URL used in the links of verification/reset emails
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:3000")

    # --- App ---
    ENV: str = os.getenv("ENV", "development")  # "development" | "production"

    # --- Chapter file uploads ---
    # NOTE: on Render's free tier this local folder is wiped on every
    # redeploy/restart. Fine for early development; before real pilot
    # usage, switch this to cloud storage (S3/Cloudinary/etc).
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", "uploads/chapters")
    MAX_UPLOAD_SIZE_MB: int = int(os.getenv("MAX_UPLOAD_SIZE_MB", "20"))

    # --- Generated media (Week 7 extended: real rendered videos) ---
    # NOTE: same caveat as UPLOAD_DIR - on Render's free tier this is wiped
    # on every redeploy/restart. Fine for development; switch to cloud
    # storage before real pilot usage.
    GENERATED_MEDIA_DIR: str = os.getenv("GENERATED_MEDIA_DIR", "generated_media")


settings = Settings()
