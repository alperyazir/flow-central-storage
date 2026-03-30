# Database Schema Documentation

This document describes the PostgreSQL database schema for the Flow Central Storage application.

## Overview

The database uses PostgreSQL with SQLAlchemy ORM. The schema supports a normalized data model where publishers own books, and both can have associated assets in MinIO storage.

## Tables

### publishers

Represents educational content publishers.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PRIMARY KEY, AUTO_INCREMENT | Unique identifier |
| `name` | VARCHAR(255) | NOT NULL, UNIQUE, INDEXED | URL-safe identifier (e.g., "universal-elt") |
| `display_name` | VARCHAR(255) | NOT NULL | Human-readable name (e.g., "Universal ELT") |
| `description` | TEXT | NULLABLE | Publisher description |
| `logo_url` | VARCHAR(512) | NULLABLE | URL to publisher logo |
| `contact_email` | VARCHAR(255) | NULLABLE | Contact email address |
| `status` | VARCHAR(50) | NOT NULL, DEFAULT 'active' | Status: 'active', 'inactive', 'deleted' |
| `created_at` | TIMESTAMP WITH TIME ZONE | NOT NULL, DEFAULT NOW() | Creation timestamp |
| `updated_at` | TIMESTAMP WITH TIME ZONE | NOT NULL, DEFAULT NOW() | Last update timestamp |

**Indexes:**
- Primary key on `id`
- Unique index on `name`

**Relationships:**
- One-to-many with `books` table

### books

Represents educational book metadata records.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PRIMARY KEY, AUTO_INCREMENT | Unique identifier |
| `publisher_id` | INTEGER | FOREIGN KEY → publishers.id, NOT NULL | Reference to publisher |
| `book_name` | VARCHAR(255) | NOT NULL | URL-safe book identifier |
| `book_title` | VARCHAR(255) | NULLABLE | Human-readable book title |
| `book_cover` | VARCHAR(512) | NULLABLE | URL to book cover image |
| `activity_count` | INTEGER | NULLABLE | Deprecated: Total number of activities |
| `activity_details` | JSONB | NULLABLE | Activity type frequency map |
| `total_size` | BIGINT | NULLABLE | Total size in bytes of book assets |
| `language` | VARCHAR(64) | NOT NULL | Language code (e.g., "en", "tr") |
| `category` | VARCHAR(128) | NULLABLE | Book category |
| `status` | ENUM | NOT NULL, DEFAULT 'draft' | Status: 'draft', 'published', 'archived' |
| `created_at` | TIMESTAMP WITH TIME ZONE | NOT NULL, DEFAULT NOW() | Creation timestamp |
| `updated_at` | TIMESTAMP WITH TIME ZONE | NOT NULL, DEFAULT NOW() | Last update timestamp |

**Indexes:**
- Primary key on `id`
- Foreign key index on `publisher_id`

**Relationships:**
- Many-to-one with `publishers` table via `publisher_id`

**Enum Values:**
- `status`: 'draft', 'published', 'archived'

### users

Represents admin panel users for authentication.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PRIMARY KEY, AUTO_INCREMENT | Unique identifier |
| `email` | VARCHAR(255) | NOT NULL, UNIQUE | User email address |
| `hashed_password` | VARCHAR(255) | NOT NULL | Bcrypt hashed password |
| `is_active` | BOOLEAN | NOT NULL, DEFAULT TRUE | Account active status |
| `created_at` | TIMESTAMP WITH TIME ZONE | NOT NULL, DEFAULT NOW() | Creation timestamp |

**Indexes:**
- Primary key on `id`
- Unique index on `email`

## Entity Relationship Diagram

```
┌─────────────────┐
│   publishers    │
├─────────────────┤
│ id (PK)         │
│ name (UNIQUE)   │────┐
│ display_name    │    │
│ description     │    │
│ logo_url        │    │
│ contact_email   │    │
│ status          │    │
│ created_at      │    │
│ updated_at      │    │
└─────────────────┘    │
                       │ 1
                       │
                       │ has many
                       │
                       │ N
┌─────────────────┐    │
│     books       │    │
├─────────────────┤    │
│ id (PK)         │    │
│ publisher_id ◄──────┘
│ (FK)            │
│ book_name       │
│ book_title      │
│ book_cover      │
│ activity_count  │
│ activity_details│
│ total_size      │
│ language        │
│ category        │
│ status          │
│ created_at      │
│ updated_at      │
└─────────────────┘

┌─────────────────┐
│     users       │
├─────────────────┤
│ id (PK)         │
│ email (UNIQUE)  │
│ hashed_password │
│ is_active       │
│ created_at      │
└─────────────────┘
```

## Relationships

### Publisher → Books (One-to-Many)

A publisher can have multiple books. Each book must belong to exactly one publisher.

**SQLAlchemy Definition:**

```python
# In Publisher model
from sqlalchemy.orm import Mapped, relationship

class Publisher(Base):
    __tablename__ = "publishers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    # ... other fields

    # Relationship
    books: Mapped[list["Book"]] = relationship("Book", back_populates="publisher_rel")

# In Book model
class Book(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(primary_key=True)
    publisher_id: Mapped[int] = mapped_column(ForeignKey("publishers.id"), nullable=False)
    # ... other fields

    # Relationship
    publisher_rel: Mapped["Publisher"] = relationship("Publisher", back_populates="books")

    # Convenience property for backward compatibility
    @property
    def publisher(self) -> str:
        """Get publisher name from relationship."""
        return self.publisher_rel.name
```

## Migration History

### 20251214_01_create_publishers_table_normalize_schema.py

**Date:** 2025-12-14

**Changes:**
- Created `publishers` table with columns: id, name, display_name, description, logo_url, contact_email, status, created_at, updated_at
- Migrated existing unique publisher names from `books.publisher` column to new `publishers` table
- Added `publisher_id` foreign key column to `books` table
- Populated `publisher_id` values by matching book records to publishers by name

### 20251214_02_drop_publisher_column_make_publisher_id_required.py

**Date:** 2025-12-14

**Changes:**
- Dropped old `books.publisher` string column
- Made `books.publisher_id` NOT NULL (required)
- Created index on `books.publisher_id` for query optimization

## Data Consistency Rules

1. **Publisher Names Must Be Unique:** The `name` field in the `publishers` table has a UNIQUE constraint.

2. **Books Require Publishers:** The `publisher_id` field in the `books` table is NOT NULL and has a foreign key constraint.

3. **Cascade Behavior:**
   - Deleting a publisher does NOT cascade delete books (soft delete pattern)
   - Books maintain referential integrity with publishers
   - Use `status='deleted'` for soft deletes instead of hard deletes

4. **Timestamps:** All tables have `created_at` and `updated_at` timestamps that are automatically managed by the database.

## Storage Integration

The database schema is integrated with MinIO object storage:

### Publisher-Based Storage Paths

Books are stored in MinIO using the publisher name as part of the path:

```
publishers/                           # Bucket name
  {publisher.name}/                   # From publishers.name
    books/                            # Asset type
      {book.book_name}/               # From books.book_name
        config.json
        pages/
        assets/
```

### Storage Path Construction

When accessing storage, the application constructs paths using the publisher relationship:

```python
# Using the Book model
book = db.query(Book).filter(Book.id == book_id).first()

# Access publisher name via relationship
publisher_name = book.publisher  # Uses @property that accesses book.publisher_rel.name

# Construct storage path
storage_path = f"{publisher_name}/books/{book.book_name}/"
```

## Query Patterns

### Get Books with Publisher Information

```python
from sqlalchemy.orm import joinedload

# Eager load publisher to avoid N+1 queries
books = db.query(Book)\
    .options(joinedload(Book.publisher_rel))\
    .filter(Book.status == BookStatusEnum.PUBLISHED)\
    .all()

for book in books:
    print(f"{book.publisher} / {book.book_name}")  # No additional query
```

### Get All Books for a Publisher

```python
# Via publisher relationship
publisher = db.query(Publisher).filter(Publisher.name == "universal-elt").first()
books = publisher.books  # Uses relationship

# Or via query
books = db.query(Book)\
    .filter(Book.publisher_id == publisher_id)\
    .all()
```

### Create Book with Publisher

```python
from app.models import Book, Publisher
from app.repositories.publisher import PublisherRepository

# Get or create publisher
publisher = PublisherRepository.get_or_create_by_name(
    db,
    "universal-elt",
    display_name="Universal ELT"
)

# Create book with publisher_id
book = Book(
    publisher_id=publisher.id,
    book_name="brains-a1",
    book_title="Brains English A1",
    language="en",
    status=BookStatusEnum.DRAFT
)
db.add(book)
db.commit()
```

## Testing Fixtures

Example test fixture for creating books with publishers:

```python
@pytest.fixture
def sample_publisher(db_session):
    """Create a sample publisher for testing."""
    publisher = Publisher(
        name="test-publisher",
        display_name="Test Publisher Inc.",
        status="active"
    )
    db_session.add(publisher)
    db_session.commit()
    db_session.refresh(publisher)
    return publisher

@pytest.fixture
def sample_book(db_session, sample_publisher):
    """Create a sample book with publisher relationship."""
    book = Book(
        publisher_id=sample_publisher.id,
        book_name="test-book",
        book_title="Test Book Title",
        language="en",
        status=BookStatusEnum.DRAFT
    )
    db_session.add(book)
    db_session.commit()
    db_session.refresh(book)
    return book
```

## Performance Considerations

1. **Eager Loading:** Use `joinedload()` when accessing publisher information for multiple books to avoid N+1 queries.

2. **Indexes:** The `publisher_id` foreign key is automatically indexed for efficient joins and lookups.

3. **JSONB Queries:** The `activity_details` JSONB column supports efficient queries:
   ```python
   # Query books with specific activity types
   books = db.query(Book).filter(
       Book.activity_details.has_key('reading')
   ).all()
   ```

4. **Pagination:** Always use `skip` and `limit` for list endpoints to avoid loading large result sets.

## Future Enhancements

Potential future schema additions:

1. **Teachers Table:** For teacher-specific content and assignments
2. **Students Table:** For student accounts and progress tracking
3. **Assignments Table:** Linking teachers, students, and book content
4. **Analytics Tables:** For usage tracking and reporting
5. **Tags/Categories:** Many-to-many relationship for flexible categorization
