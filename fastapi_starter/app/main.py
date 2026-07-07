from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import models
from .database import engine
from .routers import auth_router, items_router

# Ye line database tables ko automatically bana deti hai (agar pehle se nahi hai)
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Mera Backend API")

# CORS: Stitch se banaya frontend agar alag domain/port pe chalega to
# ye zaroori hai warna browser requests block kar dega.
# Production me allow_origins me apna actual frontend URL daal dena.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(items_router.router)


@app.get("/")
def root():
    return {"message": "API chal rahi hai! /docs pe jaake test karo"}
