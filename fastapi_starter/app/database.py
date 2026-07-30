from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import settings

# Starting with SQLite (file-based DB, no setup required)
# To use Postgres, change DATABASE_URL in .env, like this:
# DATABASE_URL=postgresql://user:password@host:5432/dbname
SQLALCHEMY_DATABASE_URL = settings.DATABASE_URL

# Render (and some other providers) give old-style "postgres://" URLs,
# but SQLAlchemy 2.x requires "postgresql://" - auto-fixing it here
# so nothing needs to be changed manually.
if SQLALCHEMY_DATABASE_URL.startswith("postgres://"):
    SQLALCHEMY_DATABASE_URL = SQLALCHEMY_DATABASE_URL.replace("postgres://", "postgresql://", 1)

is_sqlite = SQLALCHEMY_DATABASE_URL.startswith("sqlite")
connect_args = {"check_same_thread": False, "timeout": 15} if is_sqlite else {}

# pool_pre_ping tests each connection before using it, and pool_recycle
# forces old connections to be replaced. Without these, a managed Postgres
# instance (Render, Railway, etc.) that silently closes idle connections
# after a few minutes causes the NEXT request to crash with:
#   "OperationalError: server closed the connection unexpectedly"
# This is the most common cause of "it worked yesterday, now random 500s
# appear out of nowhere" on a freshly deployed backend.
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
    pool_recycle=280,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


# Provides a new DB session for each request, and closes it when done
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
