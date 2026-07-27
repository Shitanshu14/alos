from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import settings

# SQLite se start kar rahe hain (file-based DB, koi setup nahi chahiye)
# Postgres use karne ke liye .env me DATABASE_URL badal dena, jaise:
# DATABASE_URL=postgresql://user:password@host:5432/dbname
SQLALCHEMY_DATABASE_URL = settings.DATABASE_URL

# Render (aur kuch aur providers) purane style "postgres://" URL dete hain,
# lekin SQLAlchemy 2.x ko "postgresql://" chahiye - yahan auto-fix kar rahe hain
# taaki manually kuch badalna na pade.
if SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)

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
