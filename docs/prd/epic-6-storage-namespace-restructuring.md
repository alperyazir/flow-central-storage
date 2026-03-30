# **Epic 6: Storage Namespace Restructuring**

**Epic Goal**: Restructure MinIO bucket organization to support publisher-level asset isolation and activate the teachers storage namespace. This epic renames the `books` bucket to `publishers`, introduces a hierarchical path structure for publisher assets (books, logos, materials), and enables the dormant `teachers` bucket for individual teacher custom materials.

**Priority**: HIGH - Blocking downstream applications that require publisher-level isolation.

---

## **Story 6.1: Rename Books Bucket to Publishers**

**As a** system administrator, **I want** the `books` bucket renamed to `publishers`, **so that** the storage namespace accurately reflects the hierarchical ownership model where publishers contain books and other assets.

**Acceptance Criteria:**

1. Configuration constant `minio_books_bucket` is renamed to `minio_publishers_bucket` with default value `"publishers"` in `apps/api/app/core/config.py`.
2. The `minio_buckets` property returns the updated bucket list: `["publishers", "apps", "trash", "teachers"]`.
3. All references to `settings.minio_books_bucket` across routers and services are updated to `settings.minio_publishers_bucket`.
4. MinIO initialization script (`init_minio.py`) creates the `publishers` bucket on startup.
5. Environment variable support via `FCS_MINIO_PUBLISHERS_BUCKET` is available for custom bucket naming.
6. Existing tests are updated to reflect the new bucket name and all pass.

**Technical Notes:**
- Files affected: `config.py`, `routers/books.py`, `routers/storage.py`, `services/storage.py`
- Estimated references to update: ~25 occurrences

---

## **Story 6.2: Update Path Structure for Publisher Assets**

**As a** content manager, **I want** publisher storage paths to support multiple asset types (books, logos, materials), **so that** I can store publisher-level resources alongside their book content.

**Acceptance Criteria:**

1. Book storage paths follow the new hierarchy: `{publisher}/books/{book_name}/` instead of `{publisher}/{book_name}/`.
2. New path prefixes are reserved for future use: `{publisher}/logos/`, `{publisher}/materials/`.
3. API endpoints maintain backward compatibility - URLs remain `/storage/books/{publisher}/{book_name}` but internal paths use the new structure.
4. Book upload endpoints (`POST /books/upload`, `POST /books/{book_id}/upload`) write to the updated path structure.
5. Book listing and content retrieval endpoints read from the updated path structure.
6. Database model remains unchanged - `publisher` and `book_name` fields continue to map to path components.
7. Automated tests verify the new path structure for upload, list, and download operations.

**Technical Notes:**
- Path construction changes from `f"{publisher}/{book_name}/"` to `f"{publisher}/books/{book_name}/"`
- Cover image, config.json, and content retrieval must use updated paths

---

## **Story 6.3: Update Trash Logic for Publishers Bucket**

**As an** administrator, **I want** trash operations (soft-delete, restore, permanent delete) to work correctly with the renamed publishers bucket, **so that** I can manage deleted publisher content without errors.

**Acceptance Criteria:**

1. Trash path structure preserves source bucket context: `trash/publishers/{publisher}/books/{book_name}/`.
2. Trash listing logic in `services/storage.py` detects `bucket == "publishers"` and parses metadata correctly (publisher, book_name).
3. Restore operations (`POST /storage/restore`) correctly rebuild paths for the publishers bucket.
4. Permanent delete operations (`DELETE /storage/trash`) handle publishers bucket entries.
5. Item type detection returns `"book"` for entries originating from the publishers bucket.
6. Audit logging captures the correct bucket name and path for all trash operations.
7. Automated tests cover trash lifecycle (delete → list → restore, delete → list → permanent delete) for publisher content.

**Technical Notes:**
- Critical file: `apps/api/app/services/storage.py` lines 444-451 (bucket detection logic)
- Router files: `apps/api/app/routers/storage.py` lines 529-545, 601-610

---

## **Story 6.4: Activate Teachers Storage Namespace**

**As a** teacher, **I want** a dedicated storage namespace for my custom materials, **so that** I can upload and manage teaching resources independently from publisher content.

**Acceptance Criteria:**

1. The existing `teachers` bucket configuration is verified active and bucket is created on startup.
2. New API endpoints are created for teacher material management:
   - `POST /teachers/{teacher_id}/upload` - Upload material to teacher namespace
   - `GET /teachers/{teacher_id}/materials` - List teacher's materials
   - `GET /teachers/{teacher_id}/materials/{path}` - Download specific material
   - `DELETE /teachers/{teacher_id}/materials/{path}` - Soft-delete material to trash
3. Storage path structure follows: `teachers/{teacher_id}/materials/{filename}`.
4. Teacher uploads support common file types (PDF, images, audio, video, documents).
5. Trash operations support teacher materials with correct bucket detection and restore logic.
6. Admin panel displays teacher materials in a dedicated section (optional, can be deferred).
7. Automated tests cover upload, list, download, and delete operations for teacher materials.

**Technical Notes:**
- New router file: `apps/api/app/routers/teachers.py`
- Update trash logic to handle `bucket == "teachers"` detection
- Consider file size limits and allowed MIME types for teacher uploads

---

## **Compatibility Requirements**

- [x] External API URLs remain unchanged where possible
- [x] Database schema requires no changes
- [x] Existing book metadata records remain valid
- [x] Admin UI continues to function (may need path updates for API calls)

## **Risk Mitigation**

- **Primary Risk:** Existing trash entries reference old `books` bucket name
- **Mitigation:** Since this is development environment, trash can be cleared. For production, add fallback logic to detect both `books` and `publishers` bucket names in trash parsing.
- **Rollback Plan:** Revert config and code changes; bucket rename is non-destructive.

## **Definition of Done**

- [ ] All stories completed with acceptance criteria met
- [ ] All existing book operations (upload, list, download, delete, restore) verified working
- [ ] Teachers namespace operational with basic CRUD operations
- [ ] Test suite passes with updated bucket references
- [ ] API documentation updated to reflect new structure
- [ ] No regression in existing admin panel functionality

---

## **Story Manager Handoff**

"Please develop detailed user stories for this brownfield epic. Key considerations:

- This is an enhancement to an existing FastAPI + MinIO storage system
- Integration points: `config.py`, `routers/books.py`, `routers/storage.py`, `services/storage.py`
- Existing patterns to follow: Current router structure, MinIO client usage, trash handling logic
- Critical compatibility requirements: API URLs should remain stable, database unchanged
- Each story must include verification that existing book functionality remains intact

The epic should maintain system integrity while delivering publisher-level asset isolation and teacher storage activation."
