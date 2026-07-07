# Mera Backend API — FastAPI Starter

## Ye kya hai?
Ye ek ready-made FastAPI backend hai jisme already ye cheezein ban chuki hain:
- User signup/login (JWT token ke saath)
- Sample CRUD API (Items) — isko dekh ke tum apna khud ka data model bana sakte ho
- SQLite database (koi extra setup nahi chahiye, file khud ban jaati hai)
- CORS enabled — taaki tumhara Stitch wala frontend isse baat kar sake

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

## Step 4: Server chalao
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
2. `/auth/login` expand karo → "Try it out" → `username` field me email daalo (e.g. `ravi@test.com`)
   aur `password` daalo → Execute karo. Response me `access_token` milega.
3. Us token ko copy karo. Ab page ke top-right me **"Authorize"** button dabao,
   token paste karo, Authorize karo.
4. Ab `/auth/me` call karo — ye login kiye hue user ki details dikhayega.
5. `/items/` se items create/read/update/delete karo

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
