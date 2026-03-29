# Flow Central Storage API

FastAPI backend service for Flow Central Storage. This package exposes a foundational application skeleton including configuration management, database wiring, and a health-check endpoint.

## Local Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
uvicorn app.main:app --reload
```

Run tests with:

```bash
pytest
```

### Configuration

Key environment variables are defined in `.env.example`. For local admin panel development, ensure `FCS_CORS_ALLOWED_ORIGINS` lists the allowed frontend origins (comma-separated), e.g. `http://localhost:5173`.

## API Documentation

### Authentication

All API endpoints require JWT authentication via Bearer token in the `Authorization` header:

```
Authorization: Bearer <your_token>
```

### Publishers

Publishers represent educational content publishers. Each publisher can have multiple books and associated assets.

#### Create Publisher
```
POST /publishers/
```
Create a new publisher record.

**Request Body:**
```json
{
  "name": "universal-elt",
  "display_name": "Universal ELT",
  "description": "Educational publisher specializing in English language teaching",
  "logo_url": "https://example.com/logo.png",
  "contact_email": "contact@universal-elt.com",
  "status": "active"
}
```

**Response:** `PublisherResponse` with created publisher details.

#### List Publishers
```
GET /publishers/
```
Retrieve a paginated list of all publishers.

**Query Parameters:**
- `skip` (int): Number of records to skip (default: 0)
- `limit` (int): Maximum number of records to return (default: 100)

**Response:** Array of `PublisherResponse` objects.

#### Get Publisher
```
GET /publishers/{id}
```
Retrieve a specific publisher by ID.

**Response:** `PublisherResponse` with publisher details.

#### Update Publisher
```
PUT /publishers/{id}
```
Update an existing publisher.

**Request Body:** `PublisherUpdate` with fields to update.

**Response:** Updated `PublisherResponse`.

#### Delete Publisher
```
DELETE /publishers/{id}
```
Soft-delete a publisher (sets status to 'deleted').

**Response:** `PublisherResponse` with updated status.

#### List Publisher Books
```
GET /publishers/{id}/books
```
Retrieve all books associated with a specific publisher.

**Response:** Array of `BookResponse` objects.

### Books

Books belong to publishers and represent educational content packages. The publisher-book relationship is managed via `publisher_id` foreign key.

#### Create Book
```
POST /books/
```
Create a new book record. Requires a valid `publisher_id`.

**Request Body:**
```json
{
  "publisher_id": 1,
  "book_name": "brains-a1",
  "book_title": "Brains English A1",
  "book_cover": "https://example.com/cover.jpg",
  "language": "en",
  "category": "English Language Learning",
  "status": "draft"
}
```

**Response:** `BookResponse` with created book details.

#### List Books
```
GET /books/
```
Retrieve a paginated list of all books.

**Query Parameters:**
- `skip` (int): Number of records to skip (default: 0)
- `limit` (int): Maximum number of records to return (default: 100)
- `publisher_id` (int, optional): Filter by publisher ID

**Response:** Array of `BookResponse` objects with publisher relationship included.

#### Get Book
```
GET /books/{id}
```
Retrieve a specific book by ID, including publisher details.

**Response:** `BookResponse` with book and publisher details.

#### Update Book
```
PUT /books/{id}
```
Update an existing book.

**Request Body:** `BookUpdate` with fields to update.

**Response:** Updated `BookResponse`.

#### Delete Book (Soft-Delete)
```
DELETE /books/{id}
```
Soft-delete a book (sets status to 'archived').

**Response:** `BookResponse` with updated status.

#### Relocate Book to Different Publisher
```
POST /books/{id}/relocate
```
Move a book from one publisher to another, updating both database records and MinIO storage paths.

**Request Body:**
```json
{
  "new_publisher_id": 2
}
```

**Response:** `RelocationResponse` with operation details.

### Storage

The storage API manages file uploads and downloads for publisher and teacher assets.

#### List Publisher Assets
```
GET /storage/publishers/{publisher_name}/
```
List all asset types available for a publisher.

**Response:** Array of available asset type directories.

#### List Files for Asset Type
```
GET /storage/publishers/{publisher_name}/{asset_type}/
```
List all files within a specific asset type directory.

**Common asset types:**
- `books/` - Educational book content
- `images/` - Publisher images and graphics
- `documents/` - Additional documents

**Response:** Array of file objects with metadata.

#### Upload File
```
POST /storage/publishers/{publisher_name}/{asset_type}/upload
```
Upload a file to a specific publisher asset type.

**Request:** Multipart form data with file upload.

**Response:** Upload confirmation with file path.

#### Download File
```
GET /storage/publishers/{publisher_name}/{asset_type}/{file_path}
```
Download a specific file.

**Response:** File stream with appropriate content type.

#### Delete File
```
DELETE /storage/publishers/{publisher_name}/{asset_type}/{file_path}
```
Delete a file from storage.

**Response:** Deletion confirmation.

### Trash

The trash API manages soft-deleted files, allowing restoration or permanent deletion.

#### List Trashed Files
```
GET /trash/
```
List all files in the trash bucket.

**Query Parameters:**
- `prefix` (str, optional): Filter by path prefix

**Response:** Array of trashed file objects.

#### Restore File
```
POST /trash/restore
```
Restore a trashed file to its original location.

**Request Body:**
```json
{
  "object_name": "publishers/universal-elt/books/brains-a1/page1.html"
}
```

**Response:** Restoration confirmation.

#### Permanently Delete File
```
DELETE /trash/{object_name}
```
Permanently delete a file from trash.

**Response:** Deletion confirmation.

## Database Schema

### Publisher-Book Relationship

Books reference publishers via a required foreign key relationship:

```
publishers
├── id (PK)
├── name (unique, indexed)
├── display_name
├── description
├── logo_url
├── contact_email
├── status
├── created_at
└── updated_at

books
├── id (PK)
├── publisher_id (FK → publishers.id, required)
├── book_name
├── book_title
├── book_cover
├── activity_count
├── activity_details (JSONB)
├── total_size
├── language
├── category
├── status
├── created_at
└── updated_at
```

The relationship is defined in SQLAlchemy as:
```python
# In Book model
publisher_id: Mapped[int] = mapped_column(ForeignKey("publishers.id"), nullable=False)
publisher_rel: Mapped["Publisher"] = relationship("Publisher", back_populates="books")

# Convenience property for accessing publisher name
@property
def publisher(self) -> str:
    return self.publisher_rel.name
```

## Storage Structure

Files are organized in MinIO with the following structure:

```
publishers/                          # Main bucket for publisher content
  {publisher-name}/
    books/
      {book-name}/
        config.json                  # Book configuration
        pages/                       # Book pages
        assets/                      # Book-specific assets
    images/                          # Publisher images
    documents/                       # Publisher documents

teachers/                            # Bucket for teacher-uploaded content
  {teacher-id}/
    materials/                       # Teaching materials
    assignments/                     # Student assignments

trash/                               # Soft-deleted files
  {original-path}                    # Preserves original directory structure
```
