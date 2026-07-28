"""Tests for API key authentication.

This path runs on every API-key-authenticated request, so the tests below pin
down both its behaviour and its cost: it must not scan the api_keys table, must
not turn a read into a write, and must not leave a transaction open.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import security
from app.core.security import (
    generate_api_key,
    get_api_key_prefix,
    hash_api_key,
    invalidate_api_key_cache,
    verify_api_key_from_db,
)
from app.db.base import Base
from app.models.api_key import ApiKey


@pytest.fixture()
def session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()
    session.info["statements"] = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda conn, cursor, statement, *args: session.info["statements"].append(statement),
    )
    invalidate_api_key_cache()
    try:
        yield session
    finally:
        session.close()
        invalidate_api_key_cache()


def _seed_keys(session: Session, count: int) -> list[str]:
    """Insert ``count`` API keys and return their plaintext values."""
    raw_keys = []
    for index in range(count):
        raw = generate_api_key("prod", f"svc{index}")
        session.add(
            ApiKey(
                key_hash=hash_api_key(raw),
                key_prefix=get_api_key_prefix(raw),
                name=f"key-{index}",
                rate_limit=100,
                is_active=True,
            )
        )
        raw_keys.append(raw)
    session.commit()
    return raw_keys


def _count_statements(session: Session, func):
    session.info["statements"].clear()
    result = func()
    return result, len(session.info["statements"])


def test_valid_key_authenticates(session: Session) -> None:
    raw_keys = _seed_keys(session, 3)

    result = verify_api_key_from_db(raw_keys[1], session)

    assert result is not None
    assert result["type"] == "api_key"
    assert isinstance(result["api_key_id"], int)


def test_repeat_verification_is_served_from_cache(session: Session) -> None:
    raw_keys = _seed_keys(session, 3)
    first = verify_api_key_from_db(raw_keys[0], session)

    second, statements = _count_statements(session, lambda: verify_api_key_from_db(raw_keys[0], session))

    assert second == first
    assert statements == 0, "a cached verification must not query the database"


def test_non_api_key_token_never_reaches_the_database(session: Session) -> None:
    """A JWT-shaped token must not cost a query or a bcrypt comparison."""
    _seed_keys(session, 3)

    result, statements = _count_statements(
        session, lambda: verify_api_key_from_db("eyJhbGciOiJIUzI1NiJ9.payload.signature", session)
    )

    assert result is None
    assert statements == 0


def test_unknown_key_is_rejected_and_negatively_cached(session: Session) -> None:
    _seed_keys(session, 3)
    unknown = "dcs_prod_svc9_" + "x" * 24

    first, _ = _count_statements(session, lambda: verify_api_key_from_db(unknown, session))
    second, statements = _count_statements(session, lambda: verify_api_key_from_db(unknown, session))

    assert first is None
    assert second is None
    assert statements == 0, "a repeated bad token must not re-query the database"


def test_last_used_at_is_written_once_then_throttled(session: Session) -> None:
    raw_keys = _seed_keys(session, 1)
    stored = session.query(ApiKey).one()

    verify_api_key_from_db(raw_keys[0], session)
    # SQLite hands timestamps back without tzinfo, so compare naive values.
    first_seen = stored.last_used_at.replace(tzinfo=None) if stored.last_used_at else None
    assert first_seen is not None, "last_used_at should be recorded on first use"

    invalidate_api_key_cache()
    _, statements = _count_statements(session, lambda: verify_api_key_from_db(raw_keys[0], session))

    assert stored.last_used_at.replace(tzinfo=None) == first_seen, (
        "a throttled verification must not rewrite last_used_at"
    )
    assert statements == 1, "a throttled verification is a single SELECT, with no UPDATE"


def test_inactive_key_is_rejected(session: Session) -> None:
    raw_keys = _seed_keys(session, 1)
    session.query(ApiKey).one().is_active = False
    session.commit()
    invalidate_api_key_cache()

    assert verify_api_key_from_db(raw_keys[0], session) is None


def test_expired_key_is_rejected(session: Session) -> None:
    raw_keys = _seed_keys(session, 1)
    session.query(ApiKey).one().expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    session.commit()
    invalidate_api_key_cache()

    assert verify_api_key_from_db(raw_keys[0], session) is None


def test_key_with_mismatched_stored_prefix_still_authenticates(session: Session) -> None:
    """Rows predating consistent prefix storage must keep working."""
    raw_keys = _seed_keys(session, 2)
    stored = session.query(ApiKey).filter_by(key_prefix=get_api_key_prefix(raw_keys[0])).one()
    stored.key_prefix = "legacy-value"
    session.commit()
    invalidate_api_key_cache()

    assert verify_api_key_from_db(raw_keys[0], session) is not None


def test_verification_leaves_no_open_transaction(session: Session) -> None:
    """An open transaction would pin a PgBouncer server connection."""
    raw_keys = _seed_keys(session, 1)

    verify_api_key_from_db(raw_keys[0], session)  # writes last_used_at, commits
    assert not session.in_transaction()

    invalidate_api_key_cache()
    verify_api_key_from_db(raw_keys[0], session)  # read-only, throttled
    assert not session.in_transaction()

    invalidate_api_key_cache()
    verify_api_key_from_db("dcs_prod_nope_" + "y" * 24, session)  # read-only, rejected
    assert not session.in_transaction()


def test_invalidate_cache_forces_revalidation(session: Session) -> None:
    raw_keys = _seed_keys(session, 1)
    verify_api_key_from_db(raw_keys[0], session)

    invalidate_api_key_cache()
    result, statements = _count_statements(session, lambda: verify_api_key_from_db(raw_keys[0], session))

    assert result is not None
    assert statements > 0, "after invalidation the key must be re-checked against the database"


def test_verification_cost_does_not_grow_with_table_size(session: Session) -> None:
    """The prefix lookup keeps this O(1) instead of O(number of keys)."""
    raw_keys = _seed_keys(session, 10)

    calls: list[str] = []
    original = security.verify_api_key

    def counting_verify(api_key: str, stored_hash: str) -> bool:
        calls.append(stored_hash)
        return original(api_key, stored_hash)

    security.verify_api_key = counting_verify
    try:
        result = verify_api_key_from_db(raw_keys[7], session)
    finally:
        security.verify_api_key = original

    assert result is not None
    assert len(calls) == 1, f"expected a single bcrypt comparison, got {len(calls)}"
