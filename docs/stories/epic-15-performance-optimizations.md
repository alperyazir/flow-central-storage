# Epic 15 — Performance Optimizations

**Status:** Approved
**Created:** 2026-03-30
**Priority:** High (production readiness)

---

## Epic Goal

Optimize FCS for production-scale performance: connection pooling, caching, query optimization, async I/O, and response compression.

---

## Stories

### Story 15.1 — Nginx Gzip Compression + PGBouncer

**Goal:** Enable response compression and add connection pooling.

**Scope:**
- Add gzip config to `infrastructure/nginx/nginx.conf`
- Add PGBouncer service to docker-compose (reference: Flow Learn `/Users/alperyazir/Dev/dream-lms/pgbouncer/`)
- Create `infrastructure/pgbouncer/pgbouncer.ini` and `entrypoint.sh`
- Update DB config defaults (host: pgbouncer, port: 6432, reduced pool_size)
- Update api/worker depends_on to pgbouncer

### Story 15.2 — Fix N+1 Query + Optimize Material Stats

**Goal:** Eliminate 400+ queries in teacher listing.

**Scope:**
- Create `get_bulk_storage_stats()` in material repository (single GROUP BY query)
- Consolidate `get_storage_stats()` from 4 queries to 1-2 using conditional aggregation
- Update `list_teachers()` and `list_trashed_teachers()` to use bulk method

### Story 15.3 — Redis Cache Layer

**Goal:** Cache frequently-read data to reduce DB load.

**Scope:**
- Create `apps/api/app/services/cache.py` (Redis-based, TTL, JSON serialization)
- Cache: books list (5min), book detail (10min), publishers list/detail (10min), teachers list (5min), materials (5min)
- Invalidation on mutations (POST/PUT/DELETE)
- Key pattern: `fcs:{resource}:{id}:{params_hash}`

### Story 15.4 — Async S3 Storage Wrapper

**Goal:** Stop blocking the event loop with synchronous S3 calls.

**Scope:**
- Create `AsyncS3Client` wrapper using `asyncio.to_thread()`
- Wrap: list_objects, put_object, get_object, stat_object, remove_object, copy_object, presigned_get_object, fget_object, fput_object
- Apply to all async endpoints in books.py, publishers.py, storage.py, ai_content.py
- Keep sync client for non-async contexts (queue worker, init scripts)

---

## Story Sequence

```
15.1 (Gzip + PGBouncer) → 15.2 (N+1 fix) → 15.3 (Cache) → 15.4 (Async S3)
```

## Definition of Done

- [ ] PGBouncer handling all DB connections
- [ ] Gzip enabled for JSON/text responses
- [ ] Teacher listing < 5 queries regardless of count
- [ ] Hot endpoints cached with proper invalidation
- [ ] No sync S3 calls in async endpoints
- [ ] All tests pass, ruff clean
