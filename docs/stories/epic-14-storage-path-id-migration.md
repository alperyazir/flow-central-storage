# Epic 14 — Storage Path Migration: Name → ID

**Status:** Approved
**Created:** 2026-03-29
**Priority:** Critical (publisher rename causes data loss)

---

## Epic Goal

Migrate all S3 storage paths from `{publisher_name}/...` to `{publisher_id}/...` so that renaming a publisher never causes data loss.

---

## Problem

Currently all S3 object keys use publisher name:
```
Universal ELT/books/BRAINS/...
Universal ELT/assets/logos/logo.png
EduLlink/books/SCHACHMATT1KB/ai-data/metadata.json
```

When a publisher name is changed, **all files become inaccessible** — logos, books, AI data, everything.

## Target State

```
1/books/BRAINS/...
1/assets/logos/logo.png
2/books/SCHACHMATT1KB/ai-data/metadata.json
```

Publisher ID is immutable — rename is safe.

---

## Impact Analysis

### Files to Change (16 files, ~50 code points)

**Routers (4 files):**
- `routers/storage.py` — URL paths `/storage/books/{publisher}/{book_name}` → `/storage/books/{publisher_id}/{book_name}`, path builder function
- `routers/publishers.py` — All asset path constructions (logo, upload, download, delete)
- `routers/processing.py` — Process trigger, metadata passing
- `routers/ai_data.py` — AI data retrieval paths

**Storage Services (7 files):**
- `services/pdf/storage.py` — `_build_ai_data_path()`
- `services/segmentation/storage.py` — `_build_modules_path()`
- `services/topic_analysis/storage.py` — `_build_modules_path()`
- `services/vocabulary_extraction/storage.py` — `_build_ai_data_path()`
- `services/audio_generation/storage.py` — `_build_ai_data_path()`
- `services/ai_data/service.py` — `_build_metadata_path()`
- `services/unified_analysis/storage.py` — All path constructions

**Queue & Apps (2 files):**
- `services/queue/tasks.py` — Bundle paths, book asset paths, metadata passing
- `services/standalone_apps.py` — Bundle and book asset paths

**Repository (1 file):**
- `repositories/book.py` — `get_by_publisher_name_and_book_name()` → needs ID-based alternative

**Frontend (2 files):**
- `admin-panel/src/lib/storage.ts` — `bookStorageBasePath()` URL construction
- Various pages that construct storage URLs with publisher name

### Data Migration
- All existing S3 objects need path prefix change: `{name}/` → `{id}/`
- Migration script using rclone copy + rename

---

## Stories

### Story 14.1 — Backend Path Refactor

**Goal:** Change all backend code to use publisher_id in storage paths.

**Scope:**
- Create central path builder: `build_publisher_prefix(publisher_id: int) -> str`
- Update all 4 routers to use publisher_id in path construction
- Update all 7 storage services to accept and use publisher_id (int)
- Update queue tasks and standalone_apps
- Update book repository to support ID-based lookups
- Storage router URL change: `/storage/books/{publisher}/{book_name}` → `/storage/books/{publisher_id}/{book_name}`
- Add publisher name → ID resolution for backward compat during transition

**AC:**
1. All storage paths use publisher_id instead of name
2. No `publisher.name` used in any path construction
3. API endpoints accept publisher_id in URL
4. All existing tests updated and passing

### Story 14.2 — Frontend Path Update

**Goal:** Update admin panel to use publisher_id in storage API calls.

**Scope:**
- Update `lib/storage.ts` — `bookStorageBasePath` to use publisher_id
- Update all pages that construct storage URLs
- Update AuthenticatedImage component if it uses publisher name paths
- Ensure publisher detail, book explorer, asset download all work

**AC:**
1. All frontend storage API calls use publisher_id
2. Book covers, logos, assets load correctly
3. File downloads work
4. Admin panel build passes

### Story 14.3 — S3 Data Migration

**Goal:** Move all existing S3 objects from name-based to ID-based paths.

**Scope:**
- Create migration script that:
  - Lists all publishers with their IDs and names
  - For each publisher, copies all objects from `{name}/` to `{id}/`
  - Verifies object counts match
  - Optionally deletes old paths after verification
- Handle edge cases: spaces in names, special characters
- Document rollback procedure

**AC:**
1. All objects accessible via new ID-based paths
2. Object counts match pre/post migration
3. API serves all books, assets, AI data correctly after migration
4. Rollback procedure documented

---

## Story Sequence

```
14.1 Backend Refactor → 14.2 Frontend Update → 14.3 Data Migration
```

## Definition of Done

- [ ] No publisher name used in any S3 path construction
- [ ] Publisher rename preserves all data
- [ ] All existing data migrated to ID-based paths
- [ ] All tests pass
- [ ] E2E verified: rename publisher → books/logos/AI data still accessible
