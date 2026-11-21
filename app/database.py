"""Database engine, session factory, and initialization."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models.database_models import Base

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},  # Required for SQLite
)

SessionLocal = sessionmaker(
    autocommit=False, autoflush=False, expire_on_commit=False, bind=engine
)


def init_db() -> None:
    """Create all tables. Safe to call multiple times."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency: yields a database session and closes it after use."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
