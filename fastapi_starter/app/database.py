from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import settings

# SQLite se start kar rahe hain (file-based DB, koi setup nahi chahiye)
# Baad me Postgres use karna ho to .env me DATABASE_URL badal dena, jaise:
# DATABASE_URL=postgresql://user:password@localhost/dbname
SQLALCHEMY_DATABASE_URL = settings.DATABASE_URL

connect_args = {"check_same_thread": False} if SQLALCHEMY_DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


# Har request ke liye ek naya DB session deta hai, aur khatam hone pe close kar deta hai
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
