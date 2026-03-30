# Reliability Audit Report — Flow Central Storage

**Date:** 2026-03-26
**Auditor:** James (Dev Agent) — Claude Opus 4.6
**Scope:** Error handling, processing pipeline, webhooks, data integrity, file integrity, API contracts

---

## Executive Summary

| Severity | Count |
|----------|-------|
| Critical | 3 |
| High | 3 |
| Medium | 6 |
| Low | 2 |

---

## Findings

### CRITICAL

#### REL-C1: Processing Jobs Can Get Stuck in PROCESSING Forever
- **File:** `apps/api/app/services/queue/tasks.py:120-121`
- **Issue:** Job status set to PROCESSING before work begins. If worker crashes after this point but before final status update, job stays PROCESSING indefinitely. arq's job_timeout (3600s) kills the process but doesn't reset DB status.
- **Impact:** Users see permanently "processing" jobs with no way to recover except manual DB fix
- **Remediation:** Add background task that detects stuck jobs (status=PROCESSING for >job_timeout) and resets to FAILED

#### REL-C2: Orphaned AI Data on Book Soft Delete
- **File:** `apps/api/app/routers/books.py:227-278`
- **Issue:** When book is soft-deleted, only publisher bucket files are moved to trash. AI data files (in ai-data paths) are NOT moved. On restore, AI data is gone.
- **Impact:** Data loss — AI processing results permanently lost on soft delete + restore cycle
- **Remediation:** Include AI data paths in soft delete trash move, or clear AI data on restore and require reprocessing

#### REL-C3: Bulk Upload Has No Rollback
- **File:** `apps/api/app/routers/books.py:587-755`
- **Issue:** Bulk upload processes files sequentially. If file N fails after 1-(N-1) succeed, successful uploads remain in MinIO and DB. No transaction rollback for the batch.
- **Impact:** Partial uploads create inconsistent state — some books uploaded, some not, with no clear indication of what to clean up
- **Remediation:** Document this as expected behavior (partial success reporting exists), or add compensating cleanup for failed batches

---

### HIGH

#### REL-H1: No Auto-Disable for Failed Webhook Subscribers
- **File:** `apps/api/app/services/webhook.py:137-186`
- **Issue:** After 3 failed retries, delivery is logged as FAILED but subscription remains active. Permanently down subscribers silently miss all future events.
- **Impact:** Event loss without admin notification
- **Remediation:** Auto-disable subscription after N consecutive failures (e.g., 10). Add admin alert on permanent failure.

#### REL-H2: Silent Exception Swallowing in Multiple Locations
- **Files:** `services/queue/tasks.py:1961`, `services/material_ai_data/storage.py:223`, `routers/books.py:833`, `services/segmentation/strategies/ai.py:236`
- **Issue:** `except Exception: pass` or `except Exception: return False/0` in 6+ locations. Errors silently discarded.
- **Impact:** Failures go undetected — corrupt data accepted, features silently degraded
- **Remediation:** Add `logger.warning()` or `logger.error()` to all catch blocks. Return explicit error states instead of defaults.

#### REL-H3: No Global Exception Handler — Stack Trace Leakage
- **File:** `apps/api/app/main.py`
- **Issue:** No `@app.exception_handler(Exception)` registered. Unhandled exceptions return FastAPI's default 500 with stack trace in dev mode.
- **Impact:** Internal paths, module names, and stack traces exposed to clients
- **Remediation:** Add global exception handler returning generic 500 with request ID. Log full trace server-side.

---

### MEDIUM

#### REL-M1: Partial Processing Stage Failures Leave Mixed Data
- **File:** `apps/api/app/services/queue/tasks.py:181-216`
- **Issue:** Non-critical stage failures set PARTIAL status but don't clean up the failed stage's partial MinIO output
- **Impact:** Inconsistent AI data — some stages complete, others partially written
- **Remediation:** Clean up failed stage output before continuing

#### REL-M2: Delete-and-Reprocess Race Condition
- **File:** `apps/api/app/routers/processing.py:299-372`
- **Issue:** Cleanup and reprocess are separate operations. If cleanup fails midway, reprocess triggers on incomplete data.
- **Impact:** Corrupted reprocessing input
- **Remediation:** Make cleanup + requeue atomic, or verify cleanup completion before enqueueing

#### REL-M3: MinIO Upload Has No Retry
- **File:** `apps/api/app/services/storage.py:195-200`
- **Issue:** `client.put_object()` called once with no retry. Transient MinIO errors fail the entire upload.
- **Impact:** Temporary network issues abort uploads
- **Remediation:** Add retry with exponential backoff (2-3 attempts)

#### REL-M4: Trash Restore Doesn't Handle File Conflicts
- **File:** `apps/api/app/services/storage.py:574-645`
- **Issue:** Restore copies objects back to original location without checking if files already exist there
- **Impact:** Overwrite existing data if same-named book was re-uploaded after deletion
- **Remediation:** Check destination exists before restore; prompt or fail on conflict

#### REL-M5: Webhook Delivery Logs Grow Unbounded
- **File:** `apps/api/app/services/webhook.py`
- **Issue:** Every delivery attempt logged in DB forever. No TTL, cleanup, or archival policy.
- **Impact:** Database bloat over time with high-frequency webhook events
- **Remediation:** Add cleanup job: delete logs older than 30 days, or archive to cold storage

#### REL-M6: Corrupted ZIP Returns 0 Size Instead of Error
- **File:** `apps/api/app/routers/books.py:833-834`
- **Issue:** `_calculate_archive_size()` catches all exceptions and returns 0 instead of propagating error
- **Impact:** Corrupt uploads accepted with incorrect storage accounting
- **Remediation:** Log warning and propagate error or flag the book

---

### LOW

#### REL-L1: Health Check Exception Swallowing
- **Files:** `services/llm/base.py:221`, `services/tts/base.py:244`
- **Issue:** Provider health checks return False on any exception without logging
- **Impact:** Monitoring blind spots — can't distinguish "provider down" from "network error"
- **Remediation:** Add logger.warning for health check failures

#### REL-L2: No Request ID in Error Responses
- **File:** `apps/api/app/main.py`
- **Issue:** Error responses don't include a request/trace ID for correlation
- **Impact:** Difficult to correlate client errors with server logs
- **Remediation:** Add middleware to generate X-Request-ID header

---

## Positive Findings

- Cascade delete properly configured on Publisher → Books relationship
- Soft delete correctly filters archived items from list queries
- arq worker provides crash recovery with retry (max_tries=3, exponential backoff)
- Webhook HMAC signature properly implemented with SHA-256
- API contract fully consistent between TypeScript and Python (snake_case, nullable alignment)
- Temp file cleanup uses context managers (auto-cleanup on exit)
- Bulk upload properly reports per-file success/failure results
- ZIP corruption detected and reported with proper UploadError

---

## Priority Fix Order

1. **Immediate:** REL-C1 (stuck job detection), REL-C2 (AI data on soft delete)
2. **Immediate:** REL-H3 (global exception handler), REL-H2 (silent catches)
3. **This week:** REL-H1 (webhook auto-disable), REL-C3 (document bulk upload behavior)
4. **Soon:** REL-M2 (reprocess race), REL-M3 (MinIO retry), REL-M4 (restore conflicts)
5. **Optimization:** REL-M5 (log cleanup), REL-M6 (ZIP size error), REL-L2 (request ID)
