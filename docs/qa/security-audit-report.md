# Security Audit Report — Flow Central Storage

**Date:** 2026-03-26
**Auditor:** James (Dev Agent) — Claude Opus 4.6
**Scope:** Full API + Admin Panel + Infrastructure
**Framework:** OWASP Top 10

---

## Executive Summary

Audited 14 router files (118+ endpoints), auth system, storage layer, webhook service, and infrastructure config. Found **5 Critical**, **3 High**, **5 Medium**, and **0 Low** issues.

| Severity | Count |
|----------|-------|
| Critical | 5 |
| High | 3 |
| Medium | 5 |
| Low | 0 |
| Info | 0 |

---

## Findings

### CRITICAL

#### SEC-C1: JWT Secret Key Default "CHANGE_ME"
- **File:** `apps/api/app/core/config.py:47`
- **Issue:** `jwt_secret_key: str = "CHANGE_ME"` — if deployed without configuring .env, all JWTs use a known weak secret
- **Impact:** Complete auth bypass — attacker can forge valid JWTs
- **Remediation:** Raise startup error if `jwt_secret_key == "CHANGE_ME"`. Generate random secret in deployment scripts.

#### SEC-C2: ZIP Bomb Vulnerability — No Extraction Size Limits
- **File:** `apps/api/app/services/storage.py` (upload_book_archive, iter_zip_entries)
- **Issue:** ZIP extraction has no checks on compressed-to-uncompressed ratio or total extracted size
- **Impact:** DoS via malicious ZIP that expands to gigabytes, exhausting memory/disk
- **Remediation:** Add max extraction size limit (e.g., 2GB). Check `entry.file_size / entry.compress_size` ratio. Abort if > 100x.

#### SEC-C3: Webhook Replay Attack — No Idempotency
- **File:** `apps/api/app/services/webhook.py:115-120`
- **Issue:** No delivery ID, no timestamp freshness check, no nonce. Intercepted webhooks can be replayed indefinitely.
- **Impact:** Duplicate event processing at subscriber side
- **Remediation:** Add `X-Webhook-Delivery-ID` unique header. Include timestamp in signed payload. Track delivery IDs to prevent replay.

#### SEC-C4: MinIO Credentials Hardcoded in docker-compose
- **File:** `infrastructure/docker-compose.yml:70-72`
- **Issue:** `MINIO_ROOT_USER: dream_minio` and `MINIO_ROOT_PASSWORD: dream_minio_secret` hardcoded in version control
- **Impact:** Anyone with repo access has storage admin credentials
- **Remediation:** Move to `.env` file with `${MINIO_ROOT_USER}` / `${MINIO_ROOT_PASSWORD}` references. Document required env vars.

#### SEC-C5: Database Default Credentials in Code
- **File:** `apps/api/app/core/config.py:28-29`
- **Issue:** `database_user: str = "flow_admin"`, `database_password: str = "flow_password"` as defaults
- **Impact:** If .env misconfigured, app connects with known default credentials
- **Remediation:** Remove defaults or raise startup error if defaults unchanged.

---

### HIGH

#### SEC-H1: API Key Auth O(n) Bcrypt Iteration
- **File:** `apps/api/app/core/security.py:205-228`
- **Issue:** `verify_api_key_from_db()` loads ALL active keys and runs bcrypt on each to find a match. O(n) per request.
- **Impact:** At 100+ keys, auth latency exceeds 1s per request. At 1000+ keys, DoS via auth overhead.
- **Remediation:** Store deterministic key prefix (first 16 chars) as indexed column. Filter by prefix before bcrypt verification. Reduces to O(1) lookup + 1 bcrypt check.

#### SEC-H2: CORS Wildcard Methods and Headers
- **File:** `apps/api/app/main.py:105-111`
- **Issue:** `allow_methods=["*"]`, `allow_headers=["*"]` combined with `allow_credentials=True`
- **Impact:** Overly permissive CORS allows any origin (if misconfigured) to make credentialed requests with any method
- **Remediation:** Restrict to `["GET", "POST", "PUT", "DELETE", "OPTIONS"]` and specific headers `["Authorization", "Content-Type"]`.

#### SEC-H3: MinIO CORS Wildcard Origin
- **File:** `infrastructure/docker-compose.yml:74`
- **Issue:** `MINIO_API_CORS_ALLOW_ORIGIN: "*"` — allows any origin to access MinIO API directly
- **Impact:** Cross-origin attacks if MinIO port is exposed
- **Remediation:** Set to specific allowed origins or remove if MinIO is only accessed via API proxy.

---

### MEDIUM

#### SEC-M1: Unprotected Publisher Asset Download Endpoint
- **File:** `apps/api/app/routers/publishers.py:652-692`
- **Issue:** `GET /{publisher_id}/assets/{asset_type}/{filename}` has NO authentication guard
- **Impact:** Anyone knowing the URL can download publisher assets (logos, materials)
- **Remediation:** Add `credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme)` and `_require_admin(credentials, db)`.

#### SEC-M2: Publisher Asset Upload — Unsanitized Filename
- **File:** `apps/api/app/routers/publishers.py:547`
- **Issue:** `file.filename` used directly in MinIO object key without path sanitization
- **Impact:** Potential path traversal via crafted filename (e.g., `../../etc/passwd`)
- **Remediation:** Sanitize filename to alphanumeric + safe characters. Strip path separators.

#### SEC-M3: Book Upload — No File Size Limit
- **File:** `apps/api/app/routers/books.py:397` (upload_new_book), `books.py:297` (upload_book)
- **Issue:** `await file.read()` without size check. No server-side limit on upload size.
- **Impact:** Memory exhaustion DoS via large file upload
- **Remediation:** Check `file.size` before reading. Add `MAX_BOOK_UPLOAD_SIZE` config (e.g., 2GB).

#### SEC-M4: Missing Security Headers
- **File:** `apps/api/app/main.py`
- **Issue:** No `X-Frame-Options`, `X-Content-Type-Options`, `Strict-Transport-Security`, `Content-Security-Policy` headers
- **Impact:** Clickjacking, MIME sniffing, downgrade attacks possible
- **Remediation:** Add security headers middleware or configure in nginx reverse proxy.

#### SEC-M5: Webhook Delivery — No Circuit Breaker
- **File:** `apps/api/app/services/webhook.py:44-186`
- **Issue:** No circuit breaker for consistently failing webhook subscribers. No dead-letter queue.
- **Impact:** Permanently failed subscribers consume retry resources indefinitely
- **Remediation:** After N consecutive failures, auto-disable subscription. Add dead-letter logging.

---

## Auth Coverage Summary

| Router | Endpoints | Protected | Unprotected |
|--------|-----------|-----------|-------------|
| health.py | 1 | 0 | 1 (by design) |
| auth.py | 2 | 1 | 1 (login, by design) |
| api_keys.py | 3 | 3 | 0 |
| apps.py | 2 | 2 | 0 |
| publishers.py | 16 | 15 | **1 (SEC-M1)** |
| teachers.py | 5 | 5 | 0 |
| processing.py | 11 | 11 | 0 |
| storage.py | 9 | 9 | 0 |
| teachers_crud.py | 12 | 12 | 0 |
| ai_content.py | 7 | 7 | 0 |
| ai_data.py | 7 | 7 | 0 |
| webhooks.py | 6 | 6 | 0 |
| standalone_apps.py | 9 | 9 | 0 |
| books.py | 9 | 9 | 0 |
| **Total** | **99** | **96** | **3 (2 by design)** |

---

## Positive Findings

- Bcrypt used for API key hashing (not plain SHA)
- PBKDF2-HMAC for password hashing with 120K iterations
- JWT uses HMAC-SHA256 with proper signature verification
- Presigned URLs scoped with configurable expiry (1h default, 24h max)
- Path traversal protection in storage endpoints via `_sanitize_segment()` and `_normalize_relative_path()`
- All database access via SQLAlchemy ORM (no raw SQL injection risk)
- File type filtering in ZIP extraction (skips __MACOSX, .DS_Store, etc.)
- Webhook HMAC signature properly implements SHA-256
- Auth store properly clears sessions and persisted tokens on logout
