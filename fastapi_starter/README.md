# My Backend API — FastAPI Starter

## What is this?
This is a production-ready FastAPI backend that already includes all of the following:

**Auth**
- Signup/login with JWT — access token (15 min) + refresh token (7 days)
- `POST /auth/refresh` — renews the access token (rotates the refresh token)
- `POST /auth/logout` — revokes the refresh token
- `POST /auth/forgot-password` + `POST /auth/reset-password`
- `POST /auth/verify-email` — a verification email is sent after signup
- Duplicate email/phone → `409 Conflict` (not 500)
- Password policy: min 8 chars, upper+lower+number+special char
- Rate limiting on login (default 5/minute) to protect against brute-force attacks

**Items API**
- Search: `GET /items?search=laptop`
- Sorting: `GET /items?sort=created_at` or `?sort=-title` (`-` = descending)
- Filtering: `?owner_id=`, `?created_after=`, `?created_before=`

**Security / Production**
- Security headers (X-Frame-Options, CSP, X-Content-Type-Options, Referrer-Policy)
- Optional HTTPS redirect (`FORCE_HTTPS=true` in `.env`)
- CORS origins are now configurable via `.env` (wildcard `*` triggers a warning in production)
- `GET /health` — health check endpoint (also checks DB connectivity)
- Request logging
- All secrets live in the `.env` file — nothing hardcoded in code
- SQLite database by default (switch to Postgres by changing `DATABASE_URL` in `.env`)

## Step 1: Check that Python is installed
In the terminal, run:
```
python --version
```
If you don't have Python 3.9+, install it first.

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
At minimum, change `SECRET_KEY` to a strong random value. The rest of the
defaults are fine for development. If you want to send real emails
(verification/reset), also fill in the `SMTP_*` values — otherwise emails
are just printed to the console/log (dev mode, which is enough for testing).

## Step 5: Run the server
```
uvicorn app.main:app --reload
```

Now open these in your browser:
- http://127.0.0.1:8000  → basic check
- http://127.0.0.1:8000/docs → **THIS IS THE MOST IMPORTANT ONE**. Here you'll
  see all the APIs and can test them directly, without any extra tool
  (like Postman).

## Step 6: Test registration and login
On the `/docs` page:
1. Expand `/auth/signup` → "Try it out" → fill in this body and click Execute:
   ```json
   {
     "full_name": "Ravi Kumar",
     "email": "ravi@test.com",
     "phone": "9876543210",
     "password": "mypassword"
   }
   ```
2. Expand `/auth/login` → "Try it out" → fill in email/password → click Execute.
   The response will include `access_token`, `refresh_token`, and `expires_in` (seconds).
3. Copy the `access_token`. Now click the **"Authorize"** button at the top-right
   of the page, paste the token, and authorize.
4. Now call `/auth/me` — this will show the details of the logged-in user.
5. Use `/items/` to create/read/update/delete items — also try search/sort/filter
   (e.g. `/items/?search=laptop&sort=-created_at`)
6. After the access token expires (15 min), send your `refresh_token` to
   `/auth/refresh` to get a new access token — no need to log in again.
7. Send the refresh token to `/auth/logout` so it gets revoked.

## Next step: Connecting your frontend
Whatever UI you've built, wherever there's a button/form, you'll need to
call these APIs using `fetch()` or `axios`. For example:

```javascript
fetch("http://127.0.0.1:8000/auth/signup", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ email: "test@test.com", password: "1234" })
})
```

## Adding your own data model
Look at the `Item` table in `app/models.py` — you can create your own
new table (like `Product`, `Post`, `Task` — whatever your app needs)
following the same pattern. Then define its shape in `schemas.py`, and
create a new router file in the `routers/` folder, similar to `items_router.py`.

## Deployment (once the app is ready)
Free options: Render.com, Railway.app, or Fly.io — FastAPI can be deployed
directly on all of them.
