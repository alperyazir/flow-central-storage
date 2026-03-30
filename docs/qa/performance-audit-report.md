# Performance Audit Report — Flow Central Storage

**Date:** 2026-03-26
**Auditor:** James (Dev Agent) — Claude Opus 4.6
**Scope:** Database, Storage, Processing Pipeline, Caching, API Endpoints, Frontend

---

## Executive Summary

| Severity | Count |
|----------|-------|
| Critical | 4 |
| High | 5 |
| Medium | 6 |
| Low | 2 |

---

## Findings

### CRITICAL

#### PERF-C1: GET /books/ Has No Pagination
- **File:** `apps/api/app/routers/books.py:127-140`
- **Issue:** `list_all_books()` returns ALL books with no LIMIT/OFFSET. At 1000+ books, response size and query time scale linearly.
- **Impact:** Unbounded response size, increasing memory and latency
- **Remediation:** Add `skip` and `limit` query params, default limit=50

#### PERF-C2: Teachers List N+1 Query — 100+ DB Calls
- **File:** `apps/api/app/routers/teachers_crud.py:96-136`
- **Issue:** List endpoint loops through teachers and calls `get_storage_stats()` per teacher (4 queries each). With 100 teachers = 400+ queries.
- **Impact:** Endpoint blocks UI for seconds with many teachers
- **Remediation:** Batch-load stats with single aggregated query, or move stats to separate endpoint

#### PERF-C3: File Uploads Load Entire File Into Memory
- **File:** `apps/api/app/routers/books.py:297, 397, 620`
- **Issue:** `await file.read()` loads entire upload into memory. No size limit enforced server-side.
- **Impact:** 500MB upload = 500MB RAM. Concurrent uploads multiply risk.
- **Remediation:** Stream uploads directly to MinIO using `put_object()` with file stream, or enforce max size before read.

#### PERF-C4: ZIP Extraction Double Memory Copy
- **File:** `apps/api/app/services/storage.py:180-193`
- **Issue:** Archive bytes loaded into `BytesIO`, then each file entry read fully into another `BytesIO` before uploading to MinIO.
- **Impact:** 100MB ZIP holds ~200MB in memory during extraction
- **Remediation:** Stream extraction directly to MinIO using temp files or pipe

---

### HIGH

#### PERF-H1: Database Connection Pool Not Configured
- **File:** `apps/api/app/db/session.py:10`
- **Issue:** `create_engine()` uses SQLAlchemy defaults (pool_size=5, max_overflow=10) without explicit configuration
- **Impact:** Under concurrent load, connections may exhaust or queue excessively
- **Remediation:** Add explicit pool config: `pool_size=20, max_overflow=10, pool_timeout=30, pool_recycle=3600`

#### PERF-H2: Material Stats Makes 4 Separate Queries Per Teacher
- **File:** `apps/api/app/repositories/material.py:71-133`
- **Issue:** `get_storage_stats()` runs 4 separate aggregate queries (total, by_type, ai_processable, ai_processed)
- **Impact:** Multiplied by N teachers in list endpoint (PERF-C2)
- **Remediation:** Consolidate into 1-2 optimized queries

#### PERF-H3: No Application-Level Caching — Redis Only for Queue
- **File:** All repository/router files
- **Issue:** Redis is in the stack but used only for job queue. No caching for frequently-read data (publisher list, settings, book metadata).
- **Impact:** Every request hits DB even for rarely-changing data
- **Remediation:** Add Redis cache-aside for: publisher list (TTL 1h), global settings (TTL 2h), teacher stats (TTL 5min)

#### PERF-H4: Processing Stage Results Accumulate in Memory
- **File:** `apps/api/app/services/queue/tasks.py:127`
- **Issue:** `stage_results` dict holds all stage outputs simultaneously. No cleanup between stages.
- **Impact:** For large books with text + analysis + audio, memory stays high throughout entire job
- **Remediation:** Clear completed stage data with `del stage_results[completed_stage]` between stages

#### PERF-H5: Missing Index on books.status
- **File:** `apps/api/app/models/book.py`
- **Issue:** All list queries filter by `status != ARCHIVED` but no index on `books.status`
- **Impact:** Full table scan for every book listing
- **Remediation:** Add migration: `op.create_index("ix_books_status", "books", ["status"])`

---

### MEDIUM

#### PERF-M1: Frontend Routes Not Code-Split
- **File:** `apps/admin-panel/src/App.tsx:5-16`
- **Issue:** All 10 page components statically imported — no `React.lazy()` code splitting
- **Impact:** Single 499KB JS bundle loaded on every initial visit (146KB gzipped)
- **Remediation:** Use `React.lazy()` + `Suspense` for route-based splitting. Could reduce initial load by ~40%.

#### PERF-M2: LLM Calls Sequential, Not Batched
- **File:** `apps/api/app/services/llm/service.py:121-171`
- **Issue:** Each LLM API call is sequential with retry. No concurrent or batch processing.
- **Impact:** Processing 20 text segments = 20 sequential API calls
- **Remediation:** If provider supports batching, implement. Otherwise, add configurable concurrency with semaphore.

#### PERF-M3: No Concurrent Upload Limiting
- **File:** `apps/api/app/routers/books.py`
- **Issue:** No semaphore or rate limit on upload endpoints
- **Impact:** 3 concurrent 500MB uploads = 1.5GB memory spike
- **Remediation:** Add upload semaphore or max concurrent upload config

#### PERF-M4: Unbounded Trash Listing
- **File:** `apps/api/app/routers/storage.py:544-575`
- **Issue:** Trash listing iterates ALL trash objects without pagination
- **Impact:** Slow with large trash accumulation
- **Remediation:** Add pagination or max results limit

#### PERF-M5: Teacher Delete Missing Eager Load
- **File:** `apps/api/app/repositories/teacher.py:117-135`
- **Issue:** Accesses `teacher.materials` without explicit selectinload before delete
- **Impact:** Implicit lazy load triggers extra query
- **Remediation:** Use `get_with_materials()` before delete

#### PERF-M6: Unbounded Book File Listing
- **File:** `apps/api/app/routers/storage.py:206-220`
- **Issue:** Book content listing returns all files recursively without limit
- **Impact:** Books with 1000+ files return large responses
- **Remediation:** Add optional `max_depth` or `limit` parameter

---

### LOW

#### PERF-L1: BaseRepository.list_all() Has No Pagination
- **File:** `apps/api/app/repositories/base.py:38-42`
- **Issue:** Generic `list_all()` returns unbounded results
- **Impact:** Low (not used in main endpoints currently)
- **Remediation:** Deprecate or require explicit pagination

#### PERF-L2: Missing Index on materials.created_at
- **File:** `apps/api/app/models/material.py`
- **Issue:** `list_pending_ai_processing()` orders by `created_at` without index
- **Impact:** Minor — only affects processing queue ordering
- **Remediation:** Add index in future migration

---

## Positive Findings

- Download endpoints properly stream with 32KB chunks and Range request support (HTTP 206)
- Presigned URL implementation offloads direct downloads to MinIO
- Worker concurrency limited to 3 concurrent jobs (configurable)
- TTS batch processing uses asyncio.Semaphore for proper concurrency control
- Audio generation concurrency configurable (default 5)
- Temp file cleanup uses context managers (auto-cleanup)
- Frontend bundle size reasonable at 146KB gzipped
- Alembic migrations include indexes on key columns (teacher_id, publisher_id, status)

---

## Priority Fix Order

1. **Immediate:** PERF-C1 (books pagination), PERF-C2 (teachers N+1)
2. **Immediate:** PERF-C3 + PERF-C4 (upload memory — related fixes)
3. **This week:** PERF-H1 (pool config), PERF-H5 (books.status index)
4. **This week:** PERF-H3 (Redis caching for publishers/settings)
5. **Soon:** PERF-H2 (stats query consolidation), PERF-H4 (stage cleanup)
6. **Optimization:** PERF-M1 (code splitting), PERF-M2 (LLM batching)
