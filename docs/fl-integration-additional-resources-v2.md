# Additional Resources v2 — FL Integration Brief

**Status:** Shipped on DCS side. v2.1 (nested R2 layout) landed 2026-04-23.
**Audience:** Flow Central (FL) backend + frontend.

## TL;DR

Additional materials attached to a Book are **Books themselves** with `parent_book_id` set. No new entity, no new router — FL keeps using the existing `/books/*` endpoints and `Book*` webhooks. Four additions:

- Two new persistent fields on `Book`: `parent_book_id: int | null`, `book_type: 'standard' | 'pdf'`.
- Two response-only fields for convenience: `parent_book_name: str | null`, `r2_prefix: string`.
- One new endpoint for PDF children: `GET /books/{id}/pdf-url`.
- One new list filter: `GET /books/?parent_book_id=<id>`.

If FL was about to integrate a separate `BookAdditionalResource` entity — **don't**. That concept is gone.

## Data model

```
Book
├── id
├── book_name
├── book_title
├── publisher_id
├── publisher_slug           ◀──  response-only: publisher's URL slug
├── parent_book_id           ◀──  NEW: null for top-level books; int for children
├── parent_book_name         ◀──  NEW (response-only): parent's book_name, null for top-level
├── book_type                ◀──  NEW: 'standard' (default) | 'pdf'
├── r2_prefix                ◀──  NEW (response-only): the book's content prefix in R2
├── child_count              ◀──  NEW (response-only): number of non-archived children
└── …existing fields
```

Invariants:
- `parent_book_id` points only to top-level Books. Grandchildren are rejected.
- `book_type='pdf'` children have no `config.json`, no activities, no bundles.
- Deleting a parent cascades to children (DB FK + R2 prefix + bundles).

## R2 layout (nested, v2.1+)

Children live **under the parent's prefix**:

```
# Top-level book
{publisher_slug}/books/{book_name}/config.json
{publisher_slug}/books/{book_name}/images/...

# Child flowbook — nested under parent
{publisher_slug}/books/{parent_book_name}/additional-resources/{child_book_name}/config.json
{publisher_slug}/books/{parent_book_name}/additional-resources/{child_book_name}/images/...

# Child PDF — nested under parent
{publisher_slug}/books/{parent_book_name}/additional-resources/{child_book_name}/raw/{file}.pdf
```

Two consequences:
1. **Deleting a parent prefix** recursively removes all its children — the R2 layer models the relationship.
2. **Running `POST /books/sync-r2`** after a DB wipe rebuilds `parent_book_id` + `book_type` from path alone. No meta file needed.

### What's excluded from book downloads and bundles

Paths filtered by `should_skip_bundled_path()` from both book-ZIP downloads (`POST /books/{id}/download`) and standalone-app bundles:

- `__MACOSX/`, `.DS_Store`, `._*`, `desktop.ini`, `.keep`, `.gitkeep`, `settings.json`
- `*.fbinf`, `*.bak`, `*.tmp`
- `raw/` — PDF-child data area
- `additional-resources/` — child-book nested tree (parent bundle NEVER includes child content)
- `ai-data/`, `ai-content/` — AI processing artifacts

## Endpoints FL will use

### List top-level books

```
GET /books/
GET /books/?publisher_id=<id>
GET /books/?top_level_only=true       ← now the default
```

Response items include `parent_book_id`, `book_type`, `child_count`. For top-level books, `parent_book_id=null`.

### List children of a parent

```
GET /books/?parent_book_id=<parent_id>
```

### Fetch single book

```
GET /books/{id}
```

Always includes `parent_book_id`, `book_type`, `child_count`.

### Download flowbook child

Same as any top-level book:

```
POST /books/{id}/download     → 202 { job_id }
GET  /books/download-status/{job_id}
GET  /books/download-file/{job_id}
```

### Download PDF child

```
GET /books/{id}/pdf-url
→ 200 { "download_url": "<presigned URL, 6h>", "filename": "name.pdf", "expires_in_seconds": 21600 }
```

Returns 400 for non-PDF books, 404 if no PDF uploaded yet. The `download_url` is a direct presigned GET — hand to browser.

### Delete

```
DELETE /books/{id}?delete_bundles=true|false
```

Returns `{ job_id, status, book, children: [{ book_name, book_type }] }`. When `delete_bundles=true`, both parent and standard-child bundles are removed.

## Direct CDN URLs

Every Book response now includes `r2_prefix` — a pre-computed content
prefix that handles both flat and nested layouts automatically. FL
builds CDN URLs without any path logic:

```
# For ANY book (top-level or child, flowbook or PDF):
{CDN_BASE}/{book.r2_prefix}{asset_subpath}

# Examples:
{CDN_BASE}/{book.r2_prefix}config.json
{CDN_BASE}/{book.r2_prefix}images/{book.book_cover}
{CDN_BASE}/{book.r2_prefix}raw/{pdf_filename}        # PDF children
```

Reference values from a real child PDF response:
```json
{
  "id": 26,
  "book_name": "2_Sinif_22",
  "book_type": "pdf",
  "parent_book_id": 16,
  "parent_book_name": "Brains",
  "publisher_slug": "universal-elt",
  "r2_prefix": "universal-elt/books/Brains/additional-resources/2_Sinif_22/"
}
```

The underlying layout (for reference — FL should not need to recompose this manually):

```
Top-level:   {publisher_slug}/books/{book_name}/
Child book:  {publisher_slug}/books/{parent.book_name}/additional-resources/{book.book_name}/
```

**Preferred for PDFs:** `GET /books/{id}/pdf-url` returns a short-lived (6h) presigned URL — useful if CDN caching is undesirable. For long-lived cacheable URLs, use `{CDN_BASE}{r2_prefix}raw/{filename}` directly (FL needs to know the filename; query the object name via admin or keep it in FL's own metadata).

**One-call contract:** a single `GET /books/{id}` returns everything FL needs for UI rendering AND CDN URL construction — no follow-up parent fetch required.

## Webhooks

No new event types. `BOOK_CREATED`, `BOOK_UPDATED`, `BOOK_DELETED` fire for children too.

FL should:
- Inspect `parent_book_id` in each payload to distinguish parent vs child events.
- Branch on `book_type` for download: PDF → presigned URL flow, standard → ZIP flow.
- Treat child deletes as shrinking the parent's "attached resources" set.

Parent delete fires `BOOK_DELETED` only for the parent. FL that wants per-child cleanup should query children before delete, or request we emit per-child events on cascade (not currently done).

## Activity assignment + reporting

No changes. Children are Books, so everything binding `book_id` → activities/classes/teachers works unchanged. For PDF children, `activity_count=0` and no activities — treat as download-only.

## Migration guidance for FL

1. Add `parent_book_id`, `book_type` to Book DTOs.
2. Books lists: default `top_level_only=true` so parents and children don't mix.
3. PDF handling: branch on `book_type`. PDF → `GET /books/{id}/pdf-url`; standard → ZIP download.
4. Webhooks: no payload schema changes beyond the two new fields.
5. Direct CDN URLs (optional): fetch parent on-demand to build child paths. Prefer API endpoints.

## Open follow-ups (DCS-side)

- Per-child `BOOK_DELETED` webhook on parent cascade (can add if FL needs).
- Optional PDF attachment on flowbook children (out of scope; `book_type` stays either/or).

## Contact

Alper @ DCS. Edge cases → ping before working around them.
