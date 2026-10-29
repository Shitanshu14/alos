# ALOS backend - Dockerfile
#
# Includes ffmpeg + espeak-ng (needed for real video rendering, see
# app/services/video_rendering_service.py) - a plain "pip install"
# buildpack deployment (e.g. Render's native Python runtime) won't have
# these, so Docker is the recommended deployment path.

FROM python:3.12-slim

# System dependencies:
#   ffmpeg + espeak-ng  - local, free video rendering + narration
#   fonts-dejavu-core   - English text on the slides
#   fonts-noto-core     - Hindi/Devanagari on the slides (without it Hindi renders as
#                         empty "tofu" boxes). -core is enough and far smaller than the
#                         full `fonts-noto` metapackage used before.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    espeak-ng \
    fonts-dejavu-core \
    fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Uploaded chapters + generated media - mount a volume here in production
# so they survive restarts (see README's storage notes).
RUN mkdir -p uploads/chapters generated_media

EXPOSE 8000

# Container-level health check (the platform's own check, e.g. Render's
# healthCheckPath, is separate). Uses only the standard library.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8000'), timeout=4)"

# 1. `alembic upgrade head`  - apply database migrations (see DEPLOYMENT.md)
# 2. uvicorn with --proxy-headers / --forwarded-allow-ips="*": behind Render's /
#    any load balancer, the TCP peer is the proxy, so without this every user
#    shares ONE client IP and the per-IP login/signup rate limits would lock out
#    the whole platform at once. ("*" is safe ONLY because the container is not
#    reachable except through the platform's proxy.)
# 3. $PORT is set by the platform (Render/Heroku); defaults to 8000 locally.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
