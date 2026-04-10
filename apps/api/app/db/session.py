from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    echo=False,
    future=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
    pool_recycle=settings.db_pool_recycle,
    # Disable prepared statements for PgBouncer transaction pooling compatibility
    # None = never prepare; 0 would mean "prepare immediately" (the opposite!)
    connect_args={"prepare_threshold": None},
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


# TODO [PERF-H3]: Add Redis cache-aside layer (e.g. redis + fastapi-cache2)
#   for frequently-read, rarely-written data such as book listings and material stats.
# TODO [PERF-H4]: Stage processing currently buffers entire archives in memory.
#   Investigate streaming / chunked processing to reduce peak memory usage.


def get_db() -> Generator:
    """Provide a SQLAlchemy session scoped to the request lifecycle."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
