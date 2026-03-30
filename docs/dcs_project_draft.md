# FlowBook & Flow Central Storage - Project Brief

## Introduction

FlowBook is a cross-platform desktop app for teachers and students. It
renders books on screen and makes them interactive with activities such
as drag-and-drop fill-in-the-blanks, word matching, puzzles, audio/video
playback, and mini games. The app is portable and supports all major
platforms (Linux, macOS, Windows).

FlowBook and book data are separated. When a book dataset is placed in
the `data/books` folder, the app loads and runs that specific book. Each
book dataset includes a `config.json` that contains all metadata and
paths---page images, audio/video files, activity coordinates, and other
settings.

The goal is to build a stable, scalable, and extensible storage and
distribution system, called **Flow Central Storage**, that will manage
FlowBook builds and book datasets.

---

## Book Data Structure

    Book_Name
    ├── config.json
    ├── audio
    │   └── 1.mp3 …
    ├── video
    │   ├── 1.mp4
    │   └── 1.srt …
    ├── images
    │   ├── book_cover.png <- cover of the book
    │   └── Module1
    │       └── 1.png …
    ├── games.json   (if game required)
    ├── assets
    │   ├── game.png
    │   └── game.mp3 ...

### Metadata (stored in database)

Each book has metadata to improve search, filtering, and management
across LMS and admin panel.

- **id** (auto)
- **publisher** (who owns the book)
- **book_name** (title)
- **language** (e.g. en, tr)
- **category** (e.g. Math, English, Science)
- **version** (optional, future use)
- **status** (draft, published, archived)
- **created_at / updated_at**

### `config.json` Requirements

- Required to be present in every uploaded archive; files missing
  `config.json` are rejected.
- Accepted keys map to canonical metadata automatically. For example,
  `publisher_name` → `publisher`, `book_title` → `book_name`, and
  `subject` → `category`.
- Values are trimmed of whitespace; empty required values trigger
  actionable errors that reference `config.json`.
- Legacy `metadata.json` files may still be included to supply missing
  optional fields during the transition, but a deprecation warning is
  logged and the file will be ignored once config adoption is complete.

---

## FlowBook App Structure

    FlowBook App
    ├── macOS
    │   ├── FlowBook.app
    │   └── data
    │       └── books   (book data locates under this folder)
    │
    ├── Linux
    │   ├── FlowBook
    │   └── data
    │       ├── books   (book data locates under this folder)
    │       └── other system dependencies (libs, plugins)
    │
    ├── Windows
    │   ├── FlowBook.exe
    │   └── data
    │       ├── books   (book data locates under this folder)
    │       └── other system dependencies (libs, plugins)

---

## Flow Central Storage

### Storage Requirements

- MinIO (S3-compatible) hosted on a VPS.\

- Both FlowBook application builds and book datasets are stored in the
  same storage system.\

- Book and app data must be uploadable **as folders**, preserving open
  structure (not only zipped).\

- Admin panel will support **upload, list, and delete (soft delete)**
  functionality.\

- Deleted files/folders are moved to a **trash bucket** and retained
  for a period (e.g. 7 days) before permanent deletion.\

- Publisher-based organization:

      publisher_name/
        books/
          book1/
          book2/

- Separate bucket for application builds (`apps/`).

### API Service

- Provides upload, download, packaging, and listing functionalities.\
- Authentication: **JWT**.\
- Authorization: currently only Admin can upload/delete.\
- Logging and audit trail: every operation (upload, delete, restore)
  is logged.\
- Designed to be stateless for future scaling.

### Streaming

- Audio and video files will be streamed directly from storage.\
- MinIO supports HTTP range requests → suitable for streaming.\
- Future improvement: integrate a CDN (e.g., Cloudflare, Bunny.net)
  when traffic grows.

### Backup & Recovery

- VPS is a single point of failure in early stage.\
- Plan: implement daily/weekly backup sync to secondary storage.\
- Monitoring with Prometheus/Grafana to track disk usage, network, and
  errors.

---

## Admin Panel

The admin panel is the main interface for managing book and application
data.

### Features

- **Upload folder**: book data or FlowBook app builds.\
- **List books/apps**: view files per publisher and per application
  platform.\
- **Delete (soft delete)**: move to trash, recoverable for 7 days.\
- **Restore**: move back from trash.\
- **View metadata**: book name, grade level, language, category, etc.\
- **Edit metadata**: update metadata if incorrect.

### UI/UX

- Simple web-based interface.\
- Admin-only access for now.\
- Future support for roles (Admin, Editor, Publisher).

---

## LMS (Learning Management System)

- LMS will be a separate web application connected to Dream Central
  Storage.\
- Roles: Admin, Teacher, Student.\
- Features:
  - Admin assigns books to teachers.\
  - Teachers assign activities to students.\
  - Students complete activities and homework.\
  - System reports performance metrics per class/student.\
- Uses `config.json` from book data to define activities.\
- Can fetch images, audio, and video from storage.\
- Video/audio must be streamable to avoid large downloads.

---

## Kanban / Production Tracker

- Tracks each book's lifecycle: from raw assets → FlowBook-ready
  dataset → review → published.\
- Current process is manual (via email), replaced with a Kanban
  board.\
- Benefits: visibility and control over book production.\
- Book data may still be updated after approval (e.g. replace
  audio/video).\
- Requires version tracking for audit trail (but not enforced for book
  distribution).

---

## Risks & Considerations

1.  **Overwrite model**: replacing files directly may lead to data loss.
    Mitigation: soft delete + retention policy.\
2.  **Single VPS**: single point of failure. Mitigation: backup + future
    distributed MinIO.\
3.  **Streaming load**: heavy concurrent video usage may overload VPS.
    Mitigation: CDN in future.\
4.  **Metadata vs file system sync**: must ensure consistency (avoid
    orphaned metadata). Solution: enforce all operations via API.\
5.  **Scalability**: design APIs stateless, so system can scale
    horizontally.\
6.  **Security**: JWT auth is enough now, but role-based access will be
    required later.

---

## Conclusion

Flow Central Storage provides a unified, extensible platform for
managing FlowBook builds and book data. It separates application and
content, supports folder-based uploads, integrates with LMS, and ensures
flexible scaling for future needs. The metadata-driven approach enables
better organization, filtering, and usability across systems. We will start LMS and Kanban later, They added here just for the information while desinging the system
