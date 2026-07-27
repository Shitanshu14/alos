import logging

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

# Ye line database tables ko automatically bana deti hai (agar pehle se nahi hai)
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Mera Backend API")

# ---------- Rate limiting (slowapi) ----------
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    logger.warning("Rate limit exceeded for %s on %s", request.client.host if request.client else "unknown", request.url.path)
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Thoda ruk ke phir try karo."},
    )


# ---------- HTTPS redirect ----------
# Production me FORCE_HTTPS=true karo .env me (agar hosting provider khud
# HTTPS enforce nahi karta). Dev me ye off rehta hai warna localhost break ho jaayega.
if settings.FORCE_HTTPS:
    app.add_middleware(HTTPSRedirectMiddleware)


# ---------- Security headers ----------
# /docs aur /redoc FastAPI ke Swagger UI CDN se scripts/styles load karte hain,
# isliye unke liye CSP thoda relax rakhi hai. Baaki saare endpoints pe strict rehta hai.
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
# Stitch se banaya frontend agar alag domain/port pe chalega to ye zaroori hai
# warna browser requests block kar dega.
# Production me CORS_ORIGINS env var me apna actual frontend URL(s) daal dena -
# "*" kabhi production me use mat karna (allow_credentials=True ke saath to bilkul nahi,
# browsers ise reject bhi kar dete hain).
if "*" in settings.CORS_ORIGINS and settings.ENV == "production":
    logger.warning("CORS_ORIGINS me '*' hai production me - ise fix karo!")

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
    return {"message": "API chal rahi hai! /docs pe jaake test karo"}


@app.get("/health")
def health_check():
    """Health check endpoint - uptime monitoring / load balancer ke liye."""
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
