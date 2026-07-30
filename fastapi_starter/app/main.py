import logging
import traceback

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from sqlalchemy import text
from slowapi.errors import RateLimitExceeded

from . import models
from .database import engine, SessionLocal
from .routers import auth_router, items_router
from .config import settings
from .rate_limiter import limiter

# ---------- Logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("app")

# Creates all database tables automatically if they don't already exist
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="My Backend API")

# ---------- Rate limiting (slowapi) ----------
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    logger.warning("Rate limit exceeded for %s on %s", request.client.host if request.client else "unknown", request.url.path)
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please try again later."},
    )


# ---------- Global exception handling (crash -> error_logs table) ----------
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catches any unhandled exception (crash) here.
    The client receives a generic 500 message, while the full details
    (endpoint, method, error, traceback) are saved to the error_logs table."""
    tb_str = traceback.format_exc()
    client_ip = request.client.host if request.client else None

    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)

    db = SessionLocal()
    try:
        db.add(models.ErrorLog(
            path=request.url.path,
            method=request.method,
            error_type=type(exc).__name__,
            error_message=str(exc),
            traceback=tb_str,
            client_ip=client_ip,
        ))
        db.commit()
    except Exception:
        # If saving the error log itself fails, at least make sure we don't
        # crash again on top of the original crash - just log it
        logger.exception("Failed to save error log to database")
    finally:
        db.close()

    return JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong. Our team has been notified."},
    )


# ---------- HTTPS redirect ----------
# Set FORCE_HTTPS=true in .env for production (if the hosting provider
# doesn't already enforce HTTPS). Kept off in dev, otherwise localhost breaks.
if settings.FORCE_HTTPS:
    app.add_middleware(HTTPSRedirectMiddleware)


# ---------- Security headers ----------
# /docs and /redoc load scripts/styles from FastAPI's Swagger UI CDN,
# so their CSP is relaxed slightly. All other endpoints stay strict.
DOCS_PATHS = {"/docs", "/redoc", "/openapi.json"}


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    if request.url.path in DOCS_PATHS:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' cdn.jsdelivr.net; "
            "img-src 'self' data: fastapi.tiangolo.com; "
            "font-src 'self' data:;"
        )
    else:
        response.headers["Content-Security-Policy"] = "default-src 'self'"

    if settings.FORCE_HTTPS:
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    return response


# ---------- Request logging ----------
@app.middleware("http")
async def log_requests(request: Request, call_next):
    response = await call_next(request)
    logger.info("%s %s -> %s", request.method, request.url.path, response.status_code)
    return response


# ---------- CORS ----------
# Required if the frontend runs on a different domain/port,
# otherwise the browser will block the requests.
# In production, set your actual frontend URL(s) in the CORS_ORIGINS env var -
# never use "*" in production (especially not with allow_credentials=True,
# browsers reject that combination anyway).
if "*" in settings.CORS_ORIGINS and settings.ENV == "production":
    logger.warning("CORS_ORIGINS contains '*' in production - please fix this!")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(items_router.router)


@app.get("/")
def root():
    return {"message": "API is running! Go to /docs to test it."}


@app.get("/health")
def health_check():
    """Health check endpoint - for uptime monitoring / load balancer use."""
    db_status = "ok"
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
    except Exception:
        logger.exception("Health check: database connection failed")
        db_status = "error"

    overall_status = "ok" if db_status == "ok" else "error"
    return JSONResponse(
        status_code=200 if overall_status == "ok" else 503,
        content={"status": overall_status, "database": db_status},
    )
