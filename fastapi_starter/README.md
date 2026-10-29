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

All 16 weeks of the backend checklist are implemented. The pytest suite has
**82 tests** (all passing); `DEPLOYMENT.md` covers putting it online.
`ALOS_Development_Tracker.xlsx` is the source of truth for status, the
roadmap and the QA / production-readiness log.

| Week | What it covers |
|---|---|
| 1 | Project setup, config, DB, error handling, security headers, health check |
| 2 | Auth (signup/login/JWT/refresh/reset), ALOS roles, role-based dashboard |
| 3 | Chapter model + file upload (PDF/DOCX) + status tracking |
| 4 | Text extraction + chapter understanding (local NLP, English + Hindi) + content brief |
| 5 | Content library schema, tagging, 100+ seed items, admin CRUD |
| 6 | Concept matching, best-match selection, customization, quality check |
| 7 | No-match detection, content-type decision logic, local content generation, **real video rendering** |
| 8 | Notifications, pipeline status tracking, content access + resume |
| 9 | Progress tracking: completed-chapters, continue-learning, history |
| 10 | Parent-child profiles + child class selection + progress report |
| 11 | Share-link generation, send-to-child, classroom sharing |
| 12 | Classroom/session model, homework, auto student access |
| 13 | Concept-match + memory game data endpoints |
| 14 | Quiz generation, submission/scoring, unlimited retries |
| 15 | Bug-fixing, caching, load testing |
| 16 | Deployment configs, monitoring/logging, feedback intake |

### Production-readiness review (Oct 2026)

A review of the whole project (reading the code, then reproducing suspected
bugs by running them) found and fixed the following. Every fix has a
regression test in `tests/test_production_hardening.py`.

**Video system**
- Videos narrated and displayed *stage directions* ("Open with a relatable
  question...", "using a simple on-screen diagram") instead of teaching. The
  script is now built from the chapter's own sentences (Introduction → What
  we will learn → one Key-idea scene per concept → Recap), in English or Hindi.
- Matching handed a chapter **another chapter's video** (subject alone scored
  above the match threshold; generated videos are saved to the library, so
  the first video rendered for a subject was served to every chapter of it).
  A match now needs real concept overlap, and tag matching is word-based.
- Hindi chapters failed outright ("no meaningful words" - the word pattern was
  Latin-only; no danda `।` sentence split). Fixed.
- Rendering held one database transaction open for the whole render (other
  writers got "database is locked" on SQLite). Planning/rendering now happens
  before any write; failed runs delete their orphaned files.
- `moviepy` removed: ffmpeg is called directly (~5x faster per video-second),
  output is `faststart` mp4 + `.vtt` captions + `.jpg` poster, concurrency is
  capped (`MAX_CONCURRENT_RENDERS`).
- The Hindi font test never actually tested Hindi (an English word in the
  measured region made it pass even with "tofu" boxes). Rewritten.
- Seeded cartoons (fake CDN links) could be matched; now excluded like videos.

**Production**
- Dependencies had known CVEs (pypdf 96, pillow 65, python-multipart 14, ...);
  all upgraded, `python-jose` replaced by `PyJWT`; `pip-audit` is clean and runs in CI.
- No `.gitignore` / `.dockerignore` (a `COPY . .` would bake `.env` and the
  local DB into the image). Added.
- Behind Render's proxy every user shared one IP for rate limiting
  (`--proxy-headers` was missing). Fixed in Dockerfile and Procfile.
- `render.yaml`: open admin signup in production, missing `CORS_ORIGINS`,
  and a `disk:` block on the free plan. Fixed (see DEPLOYMENT.md).
- No migrations: Alembic added (`migrations/`), production no longer uses `create_all()`.
- Failed/interrupted chapters were dead ends: added `POST /chapters/{id}/reprocess`
  and automatic re-queue of stuck chapters at startup.
- Uploads: content is verified against the extension (magic bytes) and read in
  chunks (a huge upload could be fully loaded into memory before the size check).
- Request IDs (`X-Request-ID`) on every response and log line; `/health`
  reports ffmpeg/narration; chapter list is paginated; CI workflow added.

Still open (tracked in the spreadsheet): email verification is not enforced,
`/media` URLs are public (unguessable but not signed), `passlib` is
unmaintained, uploads/videos need object storage on any host without a disk,
`datetime.utcnow()` deprecation, scanned-PDF OCR.

---

## Roles and signup

Every role can register through `POST /auth/signup`:
`school`, `teacher`, `parent`, `student`, `user`, `admin`. Anything else gets a 422.

**Admin signup is open on purpose** - anyone can register as an admin, and an
admin can manage the content library, review feedback and see every user's
chapters. For a real deployment set `ALLOW_ADMIN_SIGNUP=false` in `.env`: that
removes only `admin` from the public list (create admins with
`python -m app.create_admin` instead). The app logs a warning at startup if
`ENV=production` and admin signup is still on. The list itself lives in
`ALLOWED_SIGNUP_ROLES` in `app/schemas.py`.

## Admin access

Admin accounts have platform-wide oversight: they can view/manage any
chapter, classroom, game, or quiz - not just ones they personally
created. This was a real bug reported directly by someone testing the
API as an admin: every chapter/classroom endpoint returned 403 unless
the admin happened to have created that exact resource themselves, and
the admin's own chapter/classroom lists silently showed 0 results
instead of everything on the platform. Fixed across
`access_control_service.py` (used by chapters/games/quizzes) and
`classrooms_router.py`. Regular (non-admin) accounts are unaffected -
still correctly isolated from each other's data.

## Security

A focused security review found and fixed the following (all covered by
regression tests in `tests/`):

- **Was fixed, now optional:** the signup endpoint originally allowed `role: "admin"`
  - anyone could self-register as an admin and get full content-library +
  feedback access. That was later re-opened **on request**: admin is a valid
  signup role again, controlled by `ALLOW_ADMIN_SIGNUP` (see "Roles and
  signup" above) - switch it off in production.
- **Fixed:** the app would silently run in production with the default
  placeholder `SECRET_KEY` if `.env` wasn't configured, letting anyone forge
  valid login tokens. It now refuses to start in production with a missing/weak
  key (see `config.py`).
- **Fixed:** only `/auth/login` was rate-limited. Signup, forgot-password, and
  chapter-upload (which triggers expensive background processing, including
  video rendering) were wide open to abuse. All four now have rate limits
  (see `.env.example` for the settings).
- **Design decision:** chapter upload is open to **any logged-in account**
  (teacher, parent, school, student, user, admin) - there is no role check.
  It still requires login (the chapter needs an owner) and is rate-limited
  per user (`CHAPTER_UPLOAD_RATE_LIMIT`), which is what caps abuse of the
  expensive processing/video pipeline. (It was briefly restricted to
  teacher/school; that restriction was removed on purpose.)
- **Fixed:** request bodies had no length limits on free-text fields (name,
  messages, chapter titles, etc.) - a minor DoS/storage-bloat vector, and
  unnecessary stored-XSS blast radius if the frontend ever renders this text
  unescaped. Reasonable max lengths added throughout `schemas.py`.

**Known gaps not yet addressed** (lower severity, worth doing before a real
public launch): email verification is tracked but never enforced anywhere;
file uploads are validated by extension only, not by inspecting actual file
content/magic bytes; there's no CSRF protection layer (acceptable for a
token-in-header API consumed by a JS frontend, but worth confirming against
your specific frontend's auth-storage approach); dependency versions haven't
been audited for known CVEs (run `pip audit` before launch).

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
  playable `.mp4`** (see `video_rendering_service.py`). The script is a short
  narrated lesson built from the chapter's own sentences; each scene becomes a
  designed slide, narrated by offline text-to-speech (espeak-ng) and encoded
  with ffmpeg - no paid API, no internet call. Each render also writes `.vtt`
  captions and a `.jpg` poster next to the `.mp4` (same name; use them for
  `<track>` / `poster=`). "Narrated slideshow" style, not animation. If rendering fails for
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
    content_library_router.py     - Week 5/11/15: browse/search + admin CRUD + seed + sharing + caching
    notifications_router.py       - Week 8: list / mark read
    progress_router.py            - Week 8/9: access, resume, completed, continue, history
    children_router.py            - Week 10/11: add child, set class, progress report, send-to-child
    sharing_router.py             - Week 11: public share-link resolve, shared-with-me
    classrooms_router.py          - Week 12: classrooms, enrollment, homework, auto content access
    games_router.py               - Week 13: concept-match + memory game data
    quizzes_router.py             - Week 14: quiz generate/submit/retry
    feedback_router.py            - Week 16: feedback intake + admin review
    items_router.py               - generic example CRUD from the original starter kit

  services/
    text_extraction.py            - Week 4: PDF/DOCX → plain text
    ai_service.py                 - Week 4: local chapter understanding
    matching_service.py           - Week 6/7: scoring, quality gate, no-match detection
    customization_service.py      - Week 6: personalize a matched item's title/description
    content_generation_service.py - Week 7: content-type decision + local generation
    video_rendering_service.py    - Week 7 extended: renders a real .mp4 (TTS + slides + ffmpeg)
    notification_service.py       - Week 8: create in-app notification (+ optional email)
    access_control_service.py     - Week 12: shared "can this user see this chapter" logic
    game_generation_service.py    - Week 13: concept-match/memory game data from a chapter
    quiz_generation_service.py    - Week 14: MCQ generation from a chapter's key concepts

  cache.py                        - Week 15: simple in-process TTL cache (see content_library_router.py)
  seed_content_library.py        - Week 5: bulk-inserts 108 starter content items

tests/                            - pytest suite - 82 tests (incl. test_production_hardening.py)
  conftest.py                     - fixtures: isolated test DB, user/auth helper, sample chapter files
  load_test.py                    - Week 15: dependency-free concurrent load test script

migrations/                       - Alembic: env.py + versions/ (0001 baseline)
alembic.ini                       - Alembic config (DB URL comes from app settings)
requirements.txt / requirements-dev.txt - runtime vs test/CI dependencies (pinned, audited)
.github/workflows/ci.yml          - tests + migration check + pip-audit
.gitignore / .dockerignore        - keep .env, DBs and uploads out of git and the image
Dockerfile                        - Week 16: ffmpeg + espeak-ng + fonts, runs migrations, proxy-aware uvicorn
render.yaml                       - Week 16: Render.com deployment blueprint
Procfile                          - Week 16: Railway/Heroku-style deploy (no video rendering)
DEPLOYMENT.md                     - Week 16: full deployment + monitoring + load-testing guide

## Data model

```
User              - login account (school/parent/teacher/student/user/admin)
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
Need Python 3.12 (what the Dockerfile and CI use).

## Step 1b: System packages for real video rendering (optional but recommended)
Story/poem/cartoon generation works with just Python packages. To get
**real rendered .mp4 videos** (not just scripts) for generated video content,
install these system packages too:
```
apt-get install -y ffmpeg espeak-ng fonts-dejavu-core fonts-noto-core
```
Narration calls the `espeak-ng` command-line tool directly (no `pyttsx3`, no
`libespeak.so.1` needed). Devanagari text is spoken with espeak-ng's Hindi
voice and drawn with Noto Sans Devanagari (`fonts-noto-core`; without it Hindi
slides show empty boxes).

`espeak-ng` is only needed for the *voice*. Without it (e.g. a Mac -
`brew install espeak-ng` - or a bare hosting tier) the video is still
rendered, just as a **silent** slideshow, and the match's `quality_notes` say
so. ffmpeg itself is optional too: if it isn't installed, the `imageio-ffmpeg`
package supplies one. The pipeline
only falls back to a script-only item if the render itself fails.

**Seeded videos are placeholders:** the 108 seed items have fake
`cdn.alos.app` video URLs. They are never used as a video match - a chapter's
video is always a real file: a real video from the library (admin-uploaded, or
rendered for an earlier chapter with the same concepts), otherwise a newly
rendered one. (Seed *cartoons* are placeholders with fake image links too, so they are
never matched either - a chapter's cartoon is generated as a script instead.
Seed stories/poems are template text and can still match.)

## Step 2: Create a virtual environment (recommended)
```
python -m venv venv
```
Activate it:
- Windows: `venv\Scripts\activate`
- Mac/Linux: `source venv/bin/activate`

## Step 3: Install dependencies
```
pip install -r requirements-dev.txt   # runtime + test dependencies
# (production installs only requirements.txt)
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
In development the tables are created automatically on startup; in production
use migrations (`alembic upgrade head`, see DEPLOYMENT.md).

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

```
pip install -r requirements-dev.txt
python -m pytest -q -W ignore
```
82 tests (~2 minutes: several render real videos). The video/Hindi tests need
the system packages from Step 1b - without them narration is skipped/silent
and the Hindi font test skips. Dependency audit: `pip-audit` (also in CI).

## Deployment
See `DEPLOYMENT.md`. Free options: Render.com, Railway.app, or Fly.io — this FastAPI app deploys
directly on any of them. Remember: on free tiers, local disk (chapter
uploads) is wiped on redeploy/restart — see the `UPLOAD_DIR` note in
`config.py` for when to switch to cloud storage (S3 etc).
