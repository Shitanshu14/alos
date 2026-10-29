# Deploying ALOS

This backend needs one thing most FastAPI tutorials don't mention: **ffmpeg
+ espeak-ng** (for real video rendering — see `README.md`'s "How the AI
parts work"). That single requirement decides which deployment path is
simplest.

## Recommended: Docker (Render, Railway, Fly.io, or any host)

The included `Dockerfile` installs everything needed (Python deps + ffmpeg +
espeak-ng + fonts). This works identically on Render, Railway, Fly.io, or
any other Docker-friendly host.

### Render.com (Docker)
1. Push this repo to GitHub.
2. Render dashboard → **New → Blueprint** → point it at the repo. Render
   reads `render.yaml` automatically and provisions the web service + a
   free Postgres database.
3. Fill in the `SMTP_*` environment variables in the dashboard if you want
   real emails sent (optional — without them, emails are just logged).
4. Deploy. First build takes a few minutes (installing ffmpeg etc).

**Free tier caveat:** persistent disks are, as far as we know, a paid-plan
feature on Render (verify in their docs) - so `render.yaml` ships with the
`disk:` block commented out. Without a disk, generated videos and
uploaded chapters will be lost on every redeploy/restart. Same goes for
Railway/Fly - check their current volume/disk offering before relying on
local storage past the free tier's limits.

### Railway.app (Docker)
1. New Project → Deploy from GitHub repo. Railway auto-detects the
   `Dockerfile`.
2. Add a Postgres plugin (or keep SQLite for a small pilot — see note below).
3. Set environment variables from `.env.example` in Railway's dashboard.
4. Add a volume mounted at `/app/generated_media` and `/app/uploads` so
   generated videos and chapter files survive restarts.

### Fly.io (Docker)
1. `fly launch` in this directory - it'll detect the Dockerfile.
2. `fly volumes create alos_media --size 1` then mount it at
   `/app/generated_media` in `fly.toml`.
3. `fly secrets set SECRET_KEY=... DATABASE_URL=...` etc.
4. `fly deploy`.

## Alternative: native Python runtime (no video rendering)

If a host's native Python buildpack is easier for you and you're OK with
generated "video" content staying as a script (not a rendered .mp4) —
skip Docker entirely:
- `Procfile` is included for Railway/Heroku-style native deploys.
- Everything else (auth, chapters, matching, generation, quizzes, games,
  classrooms...) works identically. Only the actual `.mp4` rendering step
  is skipped — it fails gracefully and falls back to script-only content
  (see `video_rendering_service.py`).

## Creating the first admin account

Admin accounts (needed for content library management and feedback review)
can **not** be created through the public signup API when `ENV=production`
(the default there; in development signup allows it unless
`ALLOW_ADMIN_SIGNUP=false`) - a deliberate security fix (see README's Security section). After deploying, create one
by running this once, on the server / against the production database:

```
python -m app.create_admin --email you@example.com --password "SomeStrongPass@123"
```

Or run it interactively (prompts for each value, hides the password input):
```
python -m app.create_admin
```

## Environment variables

Copy `.env.example` to `.env` and fill in. At minimum for production:
- `SECRET_KEY` - long random value (never reuse the dev default)
- `DATABASE_URL` - a real Postgres URL (SQLite is fine for a small pilot,
  but doesn't handle concurrent writes well under real traffic)
- `ENV=production` - enables HTTPS-redirect middleware
- `FRONTEND_URL` - used in email links and share links
- `SMTP_*` - for real email delivery (optional; without it, emails are
  logged instead of sent - fine for an early pilot, not for real users)

## Database & migrations

Development defaults to SQLite (zero setup). For production use Postgres via
`DATABASE_URL` (e.g. `postgresql://user:pass@host:5432/alos`) - the app
auto-corrects a `postgres://` URL (see `database.py`).

**Schema changes are managed with Alembic** (`migrations/`):

- With `ENV=production` the app does NOT call `create_all()` any more (it never
  ALTERs existing tables, so a model change would silently not reach a live
  database). The Docker CMD / Procfile run `alembic upgrade head` before
  starting the server.
- The baseline migration (`0001`) detects a database that was created by the
  old `create_all()` and leaves it untouched, so the first deploy of this
  version onto an existing database is safe; later migrations then apply
  normally.
- Changing a model? `alembic revision --autogenerate -m "what changed"`, read
  the generated file in `migrations/versions/`, commit it. CI runs
  `alembic upgrade head` + `alembic check` and fails if models and migrations
  disagree.

## Reverse proxy / client IP

The container starts uvicorn with `--proxy-headers --forwarded-allow-ips='*'`.
Behind Render (or any load balancer) the TCP peer is the proxy; without this
every user would share one "IP" and the per-IP login/signup rate limits would
lock everyone out together. Only use `'*'` when the app is reachable solely
through the platform's proxy (true on Render/Railway/Fly).

## Video rendering on a small instance

Each render is CPU/RAM heavy. `MAX_CONCURRENT_RENDERS` (default 1) caps how
many run at once; extra chapters wait. Renders run outside any database
transaction. If the server restarts mid-render, chapters stuck in
"processing"/"uploaded" are re-queued on startup (`STUCK_CHAPTER_MINUTES`),
and a failed chapter can be retried with `POST /chapters/{id}/reprocess`.
`GET /health` reports whether ffmpeg and narration (espeak-ng) are available.

## Pilot monitoring & logging

- `GET /health` checks DB connectivity, not just that the process is up -
  point your host's health check at this path (already set in
  `render.yaml`).
- Every request is logged with method, path, status code, and latency in
  milliseconds (see `main.py`'s `log_requests` middleware) - on most hosts
  this shows up in the platform's log viewer with no extra setup.
- Uncaught exceptions are caught by a global handler and logged with a full
  traceback (`main.py`), returning a generic 500 to the client - check logs
  for the real error, never trust the client-facing message alone.
- For anything beyond "read the logs," wire up a real error tracker (e.g.
  Sentry) - not included here to keep the stack simple for an early pilot.

## Feedback intake

Any logged-in user can submit feedback via `POST /feedback`. Review it as
an admin via `GET /feedback` (filterable by `category`/`status`) and mark
items `reviewed`/`resolved` via `PATCH /feedback/{id}/status`. Check this
regularly during a pilot - it's the most direct signal you'll get.

## Load testing

A quick, dependency-free load test is included at `tests/load_test.py` -
run it against a running instance:

```
python tests/load_test.py --url http://localhost:8000 --requests 200 --concurrency 20
```

It hits `/health` and `/content-library/` (the two most likely
high-traffic read endpoints) and prints latency percentiles + error rate.
For a serious pre-launch load test, use a real tool like
[Locust](https://locust.io) or [k6](https://k6.io) against a staging
deployment - this script is meant as a fast sanity check, not a
substitute for that.
