# Mera Backend API — FastAPI Starter

## Ye kya hai?
Ye ek production-ready FastAPI backend hai jisme ye sab already ban chuka hai:

**Auth**
- Signup/login with JWT — access token (15 min) + refresh token (7 din)
- `POST /auth/refresh` — access token renew karo (refresh token rotate hota hai)
- `POST /auth/logout` — refresh token revoke karo
- `POST /auth/forgot-password` + `POST /auth/reset-password`
- `POST /auth/verify-email` — signup ke baad verification email jaati hai
- Duplicate email/phone → `409 Conflict` (500 nahi)
- Password policy: min 8 chars, upper+lower+number+special char
- Login par rate limiting (default 5/minute) brute-force se bachne ke liye

**Items API**
- Search: `GET /items?search=laptop`
- Sorting: `GET /items?sort=created_at` ya `?sort=-title` (`-` = descending)
- Filtering: `?owner_id=`, `?created_after=`, `?created_before=`

**Security / Production**
- Security headers (X-Frame-Options, CSP, X-Content-Type-Options, Referrer-Policy)
- Optional HTTPS redirect (`FORCE_HTTPS=true` in `.env`)
- CORS origins ab `.env` se configurable hain (wildcard `*` production me warning deta hai)
- `GET /health` — health check endpoint (DB connectivity bhi check karta hai)
- Request logging
- Saari secrets `.env` file me — code me hardcoded nahi

- SQLite database by default (Postgres me switch karna ho to `.env` me `DATABASE_URL` badlo)

## Step 1: Python install check karo
Terminal me likho:
```
python --version
```
Agar Python 3.9+ nahi hai, pehle install karo.

## Step 2: Virtual environment banao (recommended)
```
python -m venv venv
```
Activate karo:
- Windows: `venv\Scripts\activate`
- Mac/Linux: `source venv/bin/activate`

## Step 3: Dependencies install karo
```
pip install -r requirements.txt
```

## Step 4: .env file banao
```
cp .env.example .env
```
Kam se kam `SECRET_KEY` ko ek random strong value se badal do. Baaki sab defaults
dev ke liye theek hain. Agar real emails bhejni hain (verification/reset), to
`SMTP_*` values bhi bharo — nahi to emails console/log me print ho jaayengi
(dev mode, testing ke liye kaafi hai).

## Step 5: Server chalao
```
uvicorn app.main:app --reload
```

Ab browser me jaake ye kholo:
- http://127.0.0.1:8000  → basic check
- http://127.0.0.1:8000/docs → **YE SABSE IMPORTANT HAI**. Yahan tumhe saari APIs
  dikhengi aur tum unhe seedha test kar sakte ho, bina kisi extra tool ke
  (jaise Postman).

## Step 5: Registration aur Login test karo
`/docs` page pe:
1. `/auth/signup` expand karo → "Try it out" → ye body bharo aur Execute karo:
   ```json
   {
     "full_name": "Ravi Kumar",
     "email": "ravi@test.com",
     "phone": "9876543210",
     "password": "mypassword"
   }
   ```
2. `/auth/login` expand karo → "Try it out" → email/password bharo → Execute karo.
   Response me `access_token`, `refresh_token`, aur `expires_in` (seconds) milega.
3. `access_token` ko copy karo. Ab page ke top-right me **"Authorize"** button dabao,
   token paste karo, Authorize karo.
4. Ab `/auth/me` call karo — ye login kiye hue user ki details dikhayega.
5. `/items/` se items create/read/update/delete karo — search/sort/filter bhi try karo
   (e.g. `/items/?search=laptop&sort=-created_at`)
6. Access token expire hone ke baad (15 min), `/auth/refresh` ko apna `refresh_token`
   bhej ke naya access token le lo — dobara login karne ki zaroorat nahi.
7. `/auth/logout` ko refresh token bhejo taaki wo revoke ho jaaye.

## Agla step: Stitch frontend se connect karna
Jo bhi UI Stitch me banaya hai, usme jahan bhi button/form hai, wahan se
`fetch()` ya `axios` use karke in APIs ko call karna hoga. Jaise:

```javascript
fetch("http://127.0.0.1:8000/auth/signup", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ email: "test@test.com", password: "1234" })
})
```

## Apna khud ka data model banana ho to
`app/models.py` me jo `Item` table hai usko dekho — usi pattern se apna
naya table (jaise `Product`, `Post`, `Task` — jo bhi tumhare app ko chahiye)
bana sakte ho. Fir `schemas.py` me uska shape define karo, aur ek naya
router file banao `routers/` folder me, jaise `items_router.py` hai.

## Deployment (jab app ready ho jaaye)
Free options: Render.com, Railway.app, ya Fly.io — sabme FastAPI directly
deploy ho jaati hai.
