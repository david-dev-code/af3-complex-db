from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.core.config import get_settings

settings = get_settings()

# Initialize Database
if settings.database_url:
    _engine = create_engine(settings.database_url, future=True, echo=False)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
else:
    _engine = None
    _SessionLocal = None


def get_db() -> Generator[Session, None, None]:
    """
    Dependency generator for database sessions.
    Yields a SQLAlchemy session and ensures it closes after the request.
    Raises RuntimeError if the database URL is not configured.
    """
    if _SessionLocal is None:
        raise RuntimeError("DATABASE_URL is not set - DB layer not initialized.")

    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()