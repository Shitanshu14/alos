from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# SQLite se start kar rahe hain (file-based DB, koi setup nahi chahiye)
# Baad me Postgres use karna ho to yahan bas URL change karna hoga, jaise:
# SQLALCHEMY_DATABASE_URL = "postgresql://user:password@localhost/dbname"
SQLALCHEMY_DATABASE_URL = "sqlite:///./app.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


# Har request ke liye ek naya DB session deta hai, aur khatam hone pe close kar deta hai
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
