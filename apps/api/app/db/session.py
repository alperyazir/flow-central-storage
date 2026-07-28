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
# expire_on_commit=False: without it every commit expires the loaded instances,
# so the next attribute access issues a fresh SELECT — an extra round-trip per
# request on hot paths, and a DetachedInstanceError once the session is closed.
# Repositories already refresh() explicitly where they need server-side values.
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


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


def release_session(session) -> None:
    """Return a request's DB connection to the pool before the response body.

    FastAPI keeps the ``get_db`` dependency open until the response has been
    fully sent, so an endpoint returning a ``StreamingResponse`` would otherwise
    hold a pooled connection for the entire download. Endpoints that are done
    with the database call this just before returning the stream; ``get_db``
    still closes the session afterwards, and closing twice is a no-op.
    """
    try:
        session.close()
    except Exception:  # pragma: no cover - defensive: never fail a download on cleanup
        pass
