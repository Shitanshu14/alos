import contextvars
import logging
import re
import traceback
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from sqlalchemy import text
from slowapi.errors import RateLimitExceeded

from . import models
from .database import engine, SessionLocal
from .routers import (
    auth_router, items_router, chapters_router, dashboard_router,
    content_library_router, notifications_router, progress_router, children_router,
    sharing_router, classrooms_router, games_router, quizzes_router, feedback_router,
)
from .config import settings
from .rate_limiter import limiter
from .services.video_rendering_service import ffmpeg_available, narration_available

# ---------- Logging (every line carries the request id) ----------
request_id_ctx: contextvars.ContextVar = contextvars.ContextVar("request_id", default="-")


class _RequestIdFilter(logging.Filter):
    """Adds %(request_id)s to every log record so one user's request can be
    followed across log lines (and matched to the X-Request-ID response header)."""
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        return True


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | req=%(request_id)s | %(message)s",
)
for _handler in logging.getLogger().handlers:
    _handler.addFilter(_RequestIdFilter())
logger = logging.getLogger("app")

# Table creation:
#   development/tests -> create_all() on startup (convenient, no setup).
#   production        -> Alembic migrations (`alembic upgrade head`, run by the
#                        Docker CMD). create_all() never ALTERs an existing table,
#                        so a model change would silently not reach a live database.
if settings.AUTO_CREATE_TABLES:
    models.Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Background jobs live in this process's memory: a restart/redeploy mid-render
    # used to leave chapters 'processing' forever. Re-queue them.
    if settings.RECOVER_STUCK_CHAPTERS_ON_STARTUP:
        try:
            chapters_router.recover_stuck_chapters()
        except Exception:
            logger.exception("Could not recover stuck chapters at startup")
    yield


app = FastAPI(title="ALOS API", lifespan=lifespan)

# Serve generated videos (Week 7 extended) so their media_url is actually
# viewable. Created here so it exists even before the first video renders.
os.makedirs(settings.GENERATED_MEDIA_DIR, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.GENERATED_MEDIA_DIR), name="media")

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
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = (time.monotonic() - start) * 1000
    logger.info("%s %s -> %s (%.1fms)", request.method, request.url.path, response.status_code, duration_ms)
    return response


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Outermost middleware (added last): assigns/propagates X-Request-ID."""
    incoming = request.headers.get("x-request-id", "")
    rid = incoming if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", incoming) else uuid.uuid4().hex[:16]
    token = request_id_ctx.set(rid)
    try:
        response = await call_next(request)
    finally:
        request_id_ctx.reset(token)
    response.headers["X-Request-ID"] = rid
    return response


# ---------- CORS ----------
# Required if the frontend runs on a different domain/port,
# otherwise the browser will block the requests.
# In production, set your actual frontend URL(s) in the CORS_ORIGINS env var -
# never use "*" in production (especially not with allow_credentials=True,
# browsers reject that combination anyway).
if "*" in settings.CORS_ORIGINS and settings.ENV == "production":
    logger.warning("CORS_ORIGINS contains '*' in production - please fix this!")

if settings.ALLOW_ADMIN_SIGNUP and settings.ENV == "production":
    logger.warning(
        "ALLOW_ADMIN_SIGNUP is on in production - anyone can register as an admin. "
        "Set ALLOW_ADMIN_SIGNUP=false unless that is really what you want."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(items_router.router)
app.include_router(chapters_router.router)
app.include_router(dashboard_router.router)
app.include_router(content_library_router.router)
app.include_router(notifications_router.router)
app.include_router(progress_router.router)
app.include_router(children_router.router)
app.include_router(sharing_router.router)
app.include_router(classrooms_router.router)
app.include_router(games_router.router)
app.include_router(quizzes_router.router)
app.include_router(feedback_router.router)


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
    # Video capabilities are informational (a host without ffmpeg/espeak-ng still
    # serves everything else) - they do NOT change the status code.
    return JSONResponse(
        status_code=200 if overall_status == "ok" else 503,
        content={
            "status": overall_status,
            "database": db_status,
            "video": {"ffmpeg": ffmpeg_available(), "narration": narration_available()},
        },
    )
