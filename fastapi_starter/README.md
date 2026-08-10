# ALOS Backend — FastAPI

Backend for **ALOS** (school chapter → auto-matched/generated learning content
pipeline). Teachers/schools upload a textbook chapter; the system understands
it, matches it against a content library (video/story/poem/cartoon), fills any
gaps by generating new content, and tracks each learner's progress through it.

**Everything below is 100% free to run** — no external/paid AI API is used
anywhere in this backend. Chapter understanding, matching, and content
generation all run on local Python logic (see "How the AI parts work" below).

---

## Project status

Built against `ALOS_Development_Tracker.xlsx`, weeks 1–10 of the backend
checklist are done and tested. Weeks 11–16 (sharing, classrooms, games,
quizzes, deployment/hardening) are not started yet.

| Week | What it covers | Status |
|---|---|---|
| 1 | Project setup, config, DB, error handling, security headers, health check | ✅ Done |
| 2 | Auth (signup/login/JWT/refresh/reset), ALOS roles, role-based dashboard | ✅ Done |
| 3 | Chapter model + file upload (PDF/DOCX) + status tracking | ✅ Done |
| 4 | Text extraction + chapter understanding (local NLP) + content brief | ✅ Done |
| 5 | Content library schema, tagging, 100+ seed items, admin CRUD | ✅ Done |
| 6 | Concept matching, best-match selection, customization, quality check | ✅ Done |
| 7 | No-match detection, content-type decision logic, local content generation | ✅ Done |
| 8 | Notifications, pipeline status tracking, content access + resume | ✅ Done |
| 9 | Progress tracking: completed-chapters, continue-learning, history | ✅ Done |
| 10 | Parent-child profiles + child class selection + progress report | ✅ Done |
| 11 | Share-link generation, send-to-child, classroom sharing | ✅ Done |
| 12 | Classrooms/homework | ⬜ Not started |
| 13 | Games (concept-match, memory) | ⬜ Not started |
| 14 | Quizzes | ⬜ Not started |
| 15 | Bug-fixing, perf, load testing | ⬜ Not started |
| 16 | Deployment, monitoring, feedback intake | ⬜ Not started |

Everything in the "Done" rows has been tested end-to-end (see "Running the
tests" below) — not just written, actually run and checked.

---

## The pipeline, end to end

```
Teacher uploads chapter (PDF/DOCX)
        │
        ▼
Week 3: Chapter saved, status = "uploaded"
        │  (background task kicks off automatically)
        ▼
Week 4: Text extracted → summary + key concepts worked out locally
        │
        ▼
Week 6: Each concept matched against the content library
        │  (video / story / poem / cartoon, scored by subject+class+tag overlap)
        ▼
Week 7: Whatever didn't match well gets generated fresh
        │  (and pushed back into the library for future chapters to reuse)
        ▼
Week 8: Chapter status = "ready", uploader notified, dashboard updated
        │
        ▼
Week 8-9: Learner opens content → progress/resume tracked →
          completed-chapters / continue-learning / history all update
        │
        ▼
Week 10: If the learner is a parent's child, the parent can see the same
         progress via their own account
```

## How the AI parts work (no paid API)

Two things that would normally call an LLM are done with local, free logic
instead — this was a deliberate choice to avoid API costs/rate limits:

- **Chapter understanding** (`app/services/ai_service.py`) — word-frequency +
  bigram extraction (classic extractive summarization), not a real "understanding"
  of meaning, but works well for structured textbook text.
- **Content generation** (`app/services/content_generation_service.py`) — template
  + concept-substitution. Story/poem come out as real short text. Cartoon comes
  out as a **script** (panel-by-panel) — no local way to render actual
  artwork, so it's clearly marked `is_generated=True` for an admin to
  illustrate later. **Video is different: it's actually rendered into a real,
  playable `.mp4`** (see `video_rendering_service.py`) — the script's scenes
  become narrated text slides via offline text-to-speech (espeak-ng) and
  ffmpeg, no paid API, no internet call. Simple "narrated slideshow" style,
  not animation — but a real video, not just a script. If rendering fails for
  any reason (e.g. a host missing ffmpeg/espeak-ng), it falls back to
  script-only content automatically instead of breaking the pipeline.

If you ever want to swap either of these for a real LLM call, both files have
one clearly-named function (`analyze_chapter_text` / `generate_content`) with a
docstring'd input/output contract — swap the internals, nothing else changes.

---

## Project structure

```
app/
  main.py                        - creates the app, registers all routers/middleware
  config.py                      - all settings, read from .env
  database.py                    - SQLAlchemy engine/session
  models.py                      - every DB table (see "Data model" below)
  schemas.py                     - every request/response shape (Pydantic)
  auth.py                        - password hashing, JWT encode/decode
  email_utils.py                 - SMTP sending (dev mode just logs the email)
  rate_limiter.py                - slowapi rate-limit config

  routers/
    auth_router.py                - signup/login/refresh/logout/reset/verify, require_admin
    dashboard_router.py           - Week 2/8: role-based dashboard
    chapters_router.py            - Week 3/4/6/7/8: upload, pipeline, brief, matches
    content_library_router.py     - Week 5: browse/search + admin CRUD + seed trigger
    notifications_router.py       - Week 8: list / mark read
    progress_router.py            - Week 8/9: access, resume, completed, continue, history
    children_router.py            - Week 10: add child, set class, progress report
    sharing_router.py             - Week 11: public share-link resolve, shared-with-me
    items_router.py               - generic example CRUD from the original starter kit

  services/
    text_extraction.py            - Week 4: PDF/DOCX → plain text
    ai_service.py                 - Week 4: local chapter understanding
    matching_service.py           - Week 6/7: scoring, quality gate, no-match detection
    customization_service.py      - Week 6: personalize a matched item's title/description
    content_generation_service.py - Week 7: content-type decision + local generation
    video_rendering_service.py    - Week 7 extended: renders a real .mp4 (TTS + slides + ffmpeg)
    notification_service.py       - Week 8: create in-app notification (+ optional email)

  seed_content_library.py        - Week 5: bulk-inserts 108 starter content items
```

## Data model

```
User              - login account (teacher/parent/student/school/admin)
Item              - generic example table from the original starter kit

Chapter           - one uploaded chapter (class, subject, name, file, status)
ChapterBrief      - Week 4 output: summary + key_concepts + suggested_content_types

ContentTag        - a normalized concept tag (e.g. "photosynthesis")
ContentItem       - one library item (video/story/poem/cartoon), tagged, subject+class
ContentMatch      - Week 6/7: per-chapter, per-content-type result
                    (matched | no_match | failed_quality | generated) + which ContentItem

Notification      - Week 8: in-app notification for a user
ContentProgress   - Week 8/9: resume position, % complete, status, per (owner, content item)
                    owner_type is "user" or "child" so the same table serves both

Child             - Week 10: a child profile under a parent account (no login of its own)
```

---

## Step 1: Check that Python is installed
```
python --version
```
Need Python 3.9+.

## Step 1b: System packages for real video rendering (optional but recommended)
Story/poem/cartoon generation works with just Python packages. To get
**real rendered .mp4 videos** (not just scripts) for generated video content,
install these system packages too:
```
apt-get install -y ffmpeg espeak-ng
```
If these aren't available (e.g. some bare-bones hosting tiers), the pipeline
still works — it just falls back to a script-only video item instead of
crashing.

## Step 2: Create a virtual environment (recommended)
```
python -m venv venv
```
Activate it:
- Windows: `venv\Scripts\activate`
- Mac/Linux: `source venv/bin/activate`

## Step 3: Install dependencies
```
pip install -r requirements.txt
```

## Step 4: Create the .env file
```
cp .env.example .env
```
At minimum, change `SECRET_KEY` to a strong random value. Everything else has
a sensible default for local development. If you want real emails
(verification/reset/notifications) sent, fill in the `SMTP_*` values —
otherwise they're just logged to the console (fine for dev/testing).

**No AI API key is needed anywhere** — see "How the AI parts work" above.

## Step 5: Run the server
```
uvicorn app.main:app --reload
```
Open:
- http://127.0.0.1:8000 → basic check
- http://127.0.0.1:8000/docs → **interactive API docs** — try every endpoint
  here without any extra tool (like Postman)

## Step 6: Seed the content library
The library starts empty. Sign up an `admin` account, log in, then call
`POST /content-library/seed` (via `/docs`, with the admin token authorized) —
this inserts 108 starter items across Science/Math/English/Social
Studies/Hindi. Safe to call more than once; it won't duplicate.

## Step 7: Try the full pipeline
1. `POST /auth/signup` with `role: "teacher"`, then `POST /auth/login`.
2. Authorize with the returned `access_token` (the "Authorize" button on `/docs`).
3. `POST /chapters/upload` — attach a PDF or DOCX, fill in class/subject/chapter_name.
4. Wait a couple seconds (processing runs in the background), then:
   - `GET /chapters/{id}/status` or `/pipeline-status` to see progress
   - `GET /chapters/{id}/brief` for the summary + key concepts
   - `GET /chapters/{id}/matches` for the 4 content types (matched or generated)
5. `GET /notifications/` — you'll have a "your chapter is ready" notification.
6. `POST /progress/access/{content_item_id}` to open a piece of content,
   `PATCH /progress/{content_item_id}` to update how far you got.
7. `GET /progress/completed-chapters`, `/continue-learning`, `/history`.

---

## Running the tests

There's no separate test suite file yet (Week 15 covers formal testing) — so
far each week has been verified with inline scripted checks against a fresh
SQLite DB, covering the full pipeline (signup → upload → processing →
matching/generation → progress → notifications → parent/child) plus access
control and edge cases (bad file types, non-owners, missing data, short/edge
chapter text). If you're continuing this project, a good Week-15 starting
point is turning those into a real `tests/` folder with pytest.

## Deployment (once ready)
Free options: Render.com, Railway.app, or Fly.io — this FastAPI app deploys
directly on any of them. Remember: on free tiers, local disk (chapter
uploads) is wiped on redeploy/restart — see the `UPLOAD_DIR` note in
`config.py` for when to switch to cloud storage (S3 etc).
