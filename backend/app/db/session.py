"""Database engine, session factory, and FastAPI dependency."""
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()


def _sqlalchemy_database_url(url: str) -> str:
    """Use the Psycopg 3 driver for provider-generated Postgres URLs.

    Railway and other managed Postgres providers commonly expose a bare
    ``postgresql://`` (or legacy ``postgres://``) URL. SQLAlchemy otherwise
    selects the psycopg2 dialect, which is not one of this project's
    dependencies.
    """
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


engine = create_engine(
    _sqlalchemy_database_url(settings.database_url),
    echo=settings.db_echo,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a DB session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
