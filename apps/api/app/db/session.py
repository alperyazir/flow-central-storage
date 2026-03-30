from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    echo=False,
    future=True,
    pool_size=5,
    max_overflow=5,
    pool_timeout=30,
    pool_recycle=3600,
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
