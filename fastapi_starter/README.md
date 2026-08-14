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
| 12 | Classroom/session model, homework, auto student access | ✅ Done |
| 13 | Concept-match + memory game data endpoints | ✅ Done |
| 14 | Quiz generation, submission/scoring, unlimited retries | ✅ Done |
| 15 | Bug-fixing, caching, load testing | ✅ Done |
| 16 | Deployment configs, monitoring/logging, feedback intake | ✅ Done |

**All 16 weeks of the backend checklist are complete and tested** - see
`tests/` for the full pytest suite (41 tests, all passing) and
`DEPLOYMENT.md` for how to actually put this online.

A full endpoint-by-endpoint edge-case audit (71 scenarios: invalid input,
IDOR/cross-user access attempts, boundary values, malformed data) was also
run across every router - see the tracker spreadsheet's "API Endpoint
Testing" sheet for the full breakdown. It found and fixed 3 more bugs
(email case-sensitivity, whitespace-only names, and out-of-range quiz
answer indices) on top of the security review below.

**"Testing 2.0"** went a level deeper - concurrency, resource leaks, and
query performance, not just input validation. Found and fixed 8 more real
bugs across two rounds (see the tracker's "Testing 2.0 - Deep Bugs" sheet):
- **Three race conditions**, two causing crashes (quiz generation, 500
  errors under concurrent access) and one causing **silent data
  corruption** (duplicate progress rows with no error at all - the most
  dangerous kind of bug, since nothing points at it happening). All three
  reproduced with actual concurrent-thread tests, not just reasoning about
  the code.
- A resource leak where a failed video render left file handles open
  indefinitely.
- A memory leak where the response cache never evicted old entries.
- Two N+1 query bugs - one of them was **38 SQL queries reduced to 2**
  (measured directly) in the chapter-matching pipeline, which runs on
  every single chapter upload.
- A password-hashing correctness bug: bcrypt silently truncates passwords
  past 72 bytes, so two different long passwords sharing the same first 72
  bytes would both work for login - confirmed with a direct hash/verify
  test, then fixed by rejecting overlong passwords instead of silently
  weakening them.

---

## Security

A focused security review found and fixed the following (all covered by
regression tests in `tests/`):

- **Fixed (critical):** the signup endpoint originally allowed `role: "admin"`
  - anyone could self-register as an admin and get full content-library +
  feedback access. Admin is no longer a valid signup role; create one via
  `python -m app.create_admin` instead (trusted operator, direct DB access only).
- **Fixed:** the app would silently run in production with the default
  placeholder `SECRET_KEY` if `.env` wasn't configured, letting anyone forge
  valid login tokens. It now refuses to start in production with a missing/weak
  key (see `config.py`).
- **Fixed:** only `/auth/login` was rate-limited. Signup, forgot-password, and
  chapter-upload (which triggers expensive background processing, including
  video rendering) were wide open to abuse. All four now have rate limits
  (see `.env.example` for the settings).
- **Fixed:** chapter upload had no role check - any logged-in account
  (including students) could trigger the processing pipeline. Restricted to
  teacher/school accounts.
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

tests/                            - pytest suite (Week 15) - 37 tests across every week's features
  conftest.py                     - fixtures: isolated test DB, user/auth helper, sample chapter files
  load_test.py                    - Week 15: dependency-free concurrent load test script

Dockerfile                        - Week 16: includes ffmpeg + espeak-ng for real video rendering
render.yaml                       - Week 16: Render.com deployment blueprint
Procfile                          - Week 16: Railway/Heroku-style deploy (no video rendering)
DEPLOYMENT.md                     - Week 16: full deployment + monitoring + load-testing guide

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
