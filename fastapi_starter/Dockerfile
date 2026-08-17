# ALOS backend - Dockerfile
#
# Includes ffmpeg + espeak-ng (needed for real video rendering, see
# app/services/video_rendering_service.py) - a plain "pip install"
# buildpack deployment (e.g. Render's native Python runtime) won't have
# these, so Docker is the recommended deployment path.

FROM python:3.12-slim

# System dependencies: ffmpeg + espeak-ng for local, free video rendering;
# fonts-dejavu-core for English text slides; fonts-noto for Hindi/Devanagari
# text slides (without this, Hindi chapter names/content render as empty
# "tofu" boxes instead of actual text - found via QA testing).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    espeak-ng \
    fonts-dejavu-core \
    fonts-noto \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Uploaded chapters + generated media - mount a volume here in production
# so they survive restarts (see README's storage notes).
RUN mkdir -p uploads/chapters generated_media

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
