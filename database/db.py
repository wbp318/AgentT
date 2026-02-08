"""
Database engine and session management for AgentT.
"""

import logging
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager

from config.settings import DATABASE_URL
from database.models import Base

logger = logging.getLogger(__name__)

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)


def _add_missing_columns():
    """Add any columns defined in models but missing from existing tables."""
    inspector = inspect(engine)
    for table_name, table in Base.metadata.tables.items():
        if not inspector.has_table(table_name):
            continue
        existing = {col["name"] for col in inspector.get_columns(table_name)}
        for column in table.columns:
            if column.name not in existing:
                col_type = column.type.compile(engine.dialect)
                with engine.begin() as conn:
                    conn.execute(text(
                        f"ALTER TABLE {table_name} ADD COLUMN {column.name} {col_type}"
                    ))
                logger.info(f"Added column {table_name}.{column.name} ({col_type})")


def init_db():
    """Create all tables. Call once at startup or via CLI."""
    Base.metadata.create_all(engine)
    _add_missing_columns()


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
