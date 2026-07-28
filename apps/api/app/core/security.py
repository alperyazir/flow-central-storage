"""Security helpers for password hashing and JWT generation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt

from app.core.config import Settings, get_settings

_PASSWORD_SCHEME = "pbkdf2_sha256"
_PASSWORD_ITERATIONS = 120_000


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def create_password_hash(password: str) -> str:
    """Hash a password using PBKDF2-HMAC (SHA-256)."""

    if not password:
        raise ValueError("Password must not be empty")

    salt = secrets.token_bytes(16)
    derived_key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PASSWORD_ITERATIONS)
    return f"{_PASSWORD_SCHEME}${_PASSWORD_ITERATIONS}${_b64encode(salt)}${_b64encode(derived_key)}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Validate a password against the stored PBKDF2 hash."""

    try:
        scheme, iteration_str, salt_b64, hash_b64 = stored_hash.split("$")
        if scheme != _PASSWORD_SCHEME:
            return False
        iterations = int(iteration_str)
        salt = _b64decode(salt_b64)
        expected = _b64decode(hash_b64)
    except (ValueError, TypeError):  # pragma: no cover - defensive
        return False

    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(derived, expected)


def create_access_token(
    *,
    subject: str,
    settings: Settings | None = None,
    expires_delta: timedelta | None = None,
    additional_claims: dict[str, Any] | None = None,
) -> str:
    """Generate a signed JWT for the provided subject."""

    active_settings = settings or get_settings()
    algorithm = active_settings.jwt_algorithm
    now = datetime.now(timezone.utc)
    expires = now + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=active_settings.jwt_access_token_expires_minutes)
    )

    header = {"alg": algorithm, "typ": "JWT"}
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    if additional_claims:
        payload.update(additional_claims)

    header_segment = _b64encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_segment = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))

    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    signature = hmac.new(
        active_settings.jwt_secret_key.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()
    signature_segment = _b64encode(signature)

    return f"{header_segment}.{payload_segment}.{signature_segment}"


def decode_access_token(token: str, *, settings: Settings | None = None) -> dict[str, Any]:
    """Decode and validate a JWT created by ``create_access_token``."""

    active_settings = settings or get_settings()
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Token structure invalid")

    header_segment, payload_segment, signature_segment = parts
    signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
    expected_signature = hmac.new(
        active_settings.jwt_secret_key.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()
    provided_signature = _b64decode(signature_segment)
    if not hmac.compare_digest(provided_signature, expected_signature):
        raise ValueError("Token signature mismatch")

    try:
        payload_data = json.loads(_b64decode(payload_segment))
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive guard
        raise ValueError("Token payload malformed") from exc

    exp = payload_data.get("exp")
    if exp is None:
        raise ValueError("Token missing expiration")
    now_ts = int(datetime.now(timezone.utc).timestamp())
    if now_ts >= int(exp):
        raise ValueError("Token expired")

    return payload_data


# API Key Management


def generate_api_key(environment: str, service: str) -> str:
    """Generate a new API key with the format: dcs_{environment}_{service}_{24_random_chars}."""
    random_part = secrets.token_urlsafe(24)[:24]
    return f"dcs_{environment}_{service}_{random_part}"


def hash_api_key(api_key: str) -> str:
    """Hash an API key using bcrypt."""
    return bcrypt.hashpw(api_key.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_api_key(api_key: str, stored_hash: str) -> bool:
    """Verify an API key against the stored bcrypt hash."""
    try:
        return bcrypt.checkpw(api_key.encode("utf-8"), stored_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def get_api_key_prefix(api_key: str) -> str:
    """Extract the first 16 characters of the API key for display purposes."""
    return api_key[:16] if len(api_key) >= 16 else api_key


def authenticate_token_or_api_key(token: str, settings: Settings | None = None) -> dict[str, Any]:
    """
    Authenticate a request using either JWT token or API key.

    Returns a dict with authentication info:
    - For JWT: {"type": "jwt", "user_id": int}
    - For API key: {"type": "api_key", "api_key_id": int}

    Raises ValueError if authentication fails.
    """
    active_settings = settings or get_settings()

    # Try JWT first
    try:
        payload = decode_access_token(token, settings=active_settings)
        subject = payload.get("sub")
        if subject is not None:
            return {"type": "jwt", "user_id": int(subject), "payload": payload}
    except ValueError:
        pass  # JWT failed, try API key

    # If JWT fails, it might be an API key
    # We need to check the database for the API key
    # This will be done in the router/dependency level where we have DB access
    raise ValueError("Authentication required - token is neither valid JWT nor API key format")


# API key verification
#
# This runs on every request that authenticates with an API key, so it is a hot
# path: it must not scan the table, must not turn a read into a write, and must
# not leave a transaction open (PgBouncer's transaction pooling pins a server
# connection until the transaction ends).

_API_KEY_TOKEN_PREFIX = "dcs_"

# fingerprint -> (monotonic expiry, api_key_id) where a ``None`` id caches a
# token already known to be invalid.
_api_key_cache: dict[str, tuple[float, int | None]] = {}
_api_key_cache_lock = threading.Lock()


def _token_fingerprint(token: str) -> str:
    """Cache key for a token — the raw secret is never kept in memory."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _api_key_cache_get(fingerprint: str) -> tuple[bool, int | None]:
    """Return ``(hit, api_key_id)`` for a cached verification result."""
    now = time.monotonic()
    with _api_key_cache_lock:
        entry = _api_key_cache.get(fingerprint)
        if entry is None:
            return False, None
        expires_at, api_key_id = entry
        if expires_at <= now:
            _api_key_cache.pop(fingerprint, None)
            return False, None
        return True, api_key_id


def _api_key_cache_put(fingerprint: str, api_key_id: int | None, ttl: int, max_entries: int) -> None:
    if ttl <= 0:
        return
    now = time.monotonic()
    with _api_key_cache_lock:
        if len(_api_key_cache) >= max_entries:
            for expired in [key for key, (exp, _) in _api_key_cache.items() if exp <= now]:
                _api_key_cache.pop(expired, None)
            if len(_api_key_cache) >= max_entries:
                _api_key_cache.clear()
        _api_key_cache[fingerprint] = (now + ttl, api_key_id)


def invalidate_api_key_cache() -> None:
    """Forget every cached verification result.

    Called after an API key is created or revoked so the change takes effect
    immediately in this process instead of after the cache TTL.
    """
    with _api_key_cache_lock:
        _api_key_cache.clear()


def _end_read_transaction(session) -> None:
    """Release the transaction opened by a read-only auth lookup.

    Left open, it would pin a PgBouncer server connection for the rest of the
    request. Skipped when the caller already has pending work in the session.
    """
    if session.new or session.dirty or session.deleted:
        return
    try:
        session.rollback()
    except Exception:  # pragma: no cover - defensive: auth must not fail on cleanup
        pass


def _as_utc(value: datetime | None) -> datetime | None:
    """Read a stored timestamp as UTC-aware.

    Rows written before the column was timezone-aware — and any backend that
    drops tzinfo — would otherwise raise ``TypeError`` when compared against an
    aware ``now``, turning a routine expiry check into a 500.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _should_refresh_last_used(last_used_at: datetime | None, now: datetime, throttle_seconds: int) -> bool:
    if last_used_at is None or throttle_seconds <= 0:
        return True
    return (now - _as_utc(last_used_at)).total_seconds() >= throttle_seconds


def verify_api_key_from_db(token: str, session, *, settings: Settings | None = None) -> dict[str, Any] | None:
    """
    Verify if the token is a valid API key by checking the database.

    Returns ``{"type": "api_key", "api_key_id": int}`` when the token is an
    active, unexpired API key, ``None`` otherwise. Meant to be called from
    routers that have DB session access.
    """
    from app.repositories.api_key import ApiKeyRepository

    active_settings = settings or get_settings()

    # Format check first: JWTs and malformed tokens stop here, without a
    # database round-trip or a bcrypt comparison.
    if not token.startswith(_API_KEY_TOKEN_PREFIX):
        return None

    fingerprint = _token_fingerprint(token)
    hit, cached_id = _api_key_cache_get(fingerprint)
    if hit:
        return None if cached_id is None else {"type": "api_key", "api_key_id": cached_id}

    repository = ApiKeyRepository()

    # Narrow to the keys sharing this token's stored prefix — normally exactly
    # one row, so one bcrypt comparison instead of one per key in the table.
    # Rows predating consistent prefix storage fall back to the full scan.
    candidates = repository.list_active_by_prefix(session, get_api_key_prefix(token))
    if not candidates:
        candidates = repository.list_active_keys(session)

    now = datetime.now(timezone.utc)
    matched = None
    for api_key in candidates:
        expires_at = _as_utc(api_key.expires_at)
        if expires_at and expires_at < now:
            continue
        if verify_api_key(token, api_key.key_hash):
            matched = api_key
            break

    if matched is None:
        _end_read_transaction(session)
        _api_key_cache_put(
            fingerprint,
            None,
            active_settings.api_key_cache_negative_ttl_seconds,
            active_settings.api_key_cache_max_entries,
        )
        return None

    # Read the id before any commit/rollback, which would expire the instance
    # and force a reload just to get it back.
    api_key_id = matched.id

    # last_used_at is bookkeeping, not correctness: only write when the stored
    # value has actually gone stale, so reads stay reads.
    if _should_refresh_last_used(matched.last_used_at, now, active_settings.api_key_last_used_throttle_seconds):
        repository.update_last_used(session, matched)
        session.commit()
    else:
        _end_read_transaction(session)

    _api_key_cache_put(
        fingerprint,
        api_key_id,
        active_settings.api_key_cache_ttl_seconds,
        active_settings.api_key_cache_max_entries,
    )
    return {"type": "api_key", "api_key_id": api_key_id}
