"""
Saari settings yahan se aati hain — environment variables se.
Production me .env file banao (.env.example ko copy karke) aur
wahan real values daalo. Kabhi bhi secrets (SECRET_KEY, SMTP password,
etc.) code me hardcode mat karo.
"""
import os
from dotenv import load_dotenv

load_dotenv()  # .env file agar exist karti hai to load kar leta hai


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
    # Production me "*" kabhi mat use karna — apna actual frontend domain daalo.
    # Comma separated: CORS_ORIGINS=https://myapp.com,https://www.myapp.com
    CORS_ORIGINS: list = _get_list("CORS_ORIGINS", ["http://localhost:3000", "http://127.0.0.1:3000"])

    # --- HTTPS redirect (production me True rakho, agar proxy/host pehle se HTTPS enforce nahi karta) ---
    FORCE_HTTPS: bool = _get_bool("FORCE_HTTPS", False)

    # --- Rate limiting ---
    LOGIN_RATE_LIMIT: str = os.getenv("LOGIN_RATE_LIMIT", "5/minute")

    # --- Email / SMTP ---
    # Agar ye configure nahi hai to emails console/log me print ho jaayenge (dev mode).
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM_EMAIL: str = os.getenv("SMTP_FROM_EMAIL", "no-reply@example.com")
    SMTP_USE_TLS: bool = _get_bool("SMTP_USE_TLS", True)

    # Frontend URLs jo verification/reset emails ke links me use honge
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:3000")

    # --- App ---
    ENV: str = os.getenv("ENV", "development")  # "development" | "production"


settings = Settings()
