"""
Database engine and session management for AgentT.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager

from config.settings import DATABASE_URL
from database.models import Base


engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)


def init_db():
    """Create all tables. Call once at startup or via CLI."""
    Base.metadata.create_all(engine)


def drop_db():
    """Drop all tables. Use with caution."""
    Base.metadata.drop_all(engine)


@contextmanager
def get_session() -> Session:
    """Context manager for database sessions with auto-commit/rollback."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db_session() -> Session:
    """Get a session for FastAPI dependency injection."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
