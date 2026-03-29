# Flow Central Storage Admin Panel

React-based admin panel for managing publishers, books, and educational content in the Flow Central Storage system.

## Features

### Publisher Management

The admin panel provides comprehensive publisher management capabilities:

- **View Publishers**: Browse all publishers with their display names, contact information, and status
- **Create Publisher**: Add new educational publishers to the system
- **Edit Publisher**: Update publisher information including:
  - Display name
  - Description
  - Logo URL
  - Contact email
  - Status (active/inactive)
- **View Publisher Details**: Access detailed publisher information and associated books
- **Publisher-Book Relationship**: View all books associated with each publisher

### Book Management

Manage educational book content with full lifecycle support:

- **View Books**: Browse all books with publisher information, status, and metadata
- **Create Book**: Add new books with:
  - Publisher selection (required)
  - Book name and title
  - Language and category
  - Cover image URL
- **Edit Book**: Update book metadata
- **Upload Book Content**: Upload ZIP archives containing book assets
- **Delete Books**: Soft-delete books (moves to trash for recovery)
- **Filter Books**: Filter books by publisher

### Asset Upload

Upload and manage various types of content:

#### Publisher Assets
- **Book Content**: Upload complete book packages as ZIP files
- **Images**: Upload publisher logos and promotional materials
- **Documents**: Upload additional publisher documents

Upload locations follow the structure:
```
publishers/{publisher-name}/books/{book-name}/
publishers/{publisher-name}/images/
publishers/{publisher-name}/documents/
```

#### Teacher Materials
- **Teaching Materials**: Upload lesson plans, worksheets
- **Student Assignments**: Upload assignment files

Upload locations:
```
teachers/{teacher-id}/materials/
teachers/{teacher-id}/assignments/
```

### Trash Management

Manage soft-deleted content:

- **View Trashed Files**: Browse all files in trash with metadata
- **Restore Files**: Recover accidentally deleted files
- **Permanent Delete**: Remove files permanently from storage
- **Retention Policy**: Files are retained in trash for 7 days by default

## Getting Started

### Prerequisites

- Node.js 18+ and npm
- Access to the Flow Central Storage API
- Valid admin credentials

### Installation

```bash
cd apps/admin-panel
npm install
```

### Configuration

Create a `.env.local` file with your API configuration:

```env
VITE_API_BASE_URL=http://localhost:8000
VITE_MINIO_EXTERNAL_URL=http://localhost:9000
```

### Development

Start the development server:

```bash
npm run dev
```

The admin panel will be available at `http://localhost:5173`.

### Build for Production

```bash
npm run build
```

## Authentication

The admin panel uses JWT-based authentication. Users must log in with valid credentials to access any management features.

Session tokens are:
- Stored in browser localStorage
- Refreshed automatically
- Required for all API requests
- Validated on every route change

## User Interface

### Navigation

The admin panel features a persistent sidebar navigation with the following sections:

- **Books**: Book listing and management
- **Publishers**: Publisher listing and management
- **Teachers**: Teacher content management
- **Trash**: Deleted file recovery

### Book Upload Workflow

1. Navigate to Books page
2. Click "Upload Book" button
3. Select publisher from dropdown
4. Choose ZIP file containing book content
5. Optionally select "Override existing" to replace existing book
6. Upload initiates automatically

### Publisher Upload Workflow

1. Navigate to Publisher detail page
2. Click "Upload" button
3. Select content type (books, images, documents)
4. Choose files to upload
5. Confirm upload

### Teacher Upload Workflow

1. Navigate to Teachers page
2. Enter teacher ID
3. Select material type (materials, assignments)
4. Choose files to upload
5. Confirm upload

## Data Model

### Publisher

```typescript
interface Publisher {
  id: number;
  name: string;              // URL-safe identifier
  display_name: string;      // Human-readable name
  description?: string;
  logo_url?: string;
  contact_email?: string;
  status: 'active' | 'inactive' | 'deleted';
  created_at: string;
  updated_at: string;
}
```

### Book

```typescript
interface Book {
  id: number;
  publisher_id: number;      // Foreign key to publisher
  publisher: string;         // Derived from relationship
  book_name: string;         // URL-safe identifier
  book_title?: string;       // Human-readable title
  book_cover?: string;
  language: string;
  category?: string;
  status: 'draft' | 'published' | 'archived';
  activity_count?: number;
  activity_details?: object;
  total_size?: number;
  created_at: string;
  updated_at: string;
}
```

## API Integration

The admin panel integrates with the Flow Central Storage API using the following endpoints:

### Publishers
- `POST /publishers/` - Create publisher
- `GET /publishers/` - List publishers
- `GET /publishers/{id}` - Get publisher details
- `PUT /publishers/{id}` - Update publisher
- `DELETE /publishers/{id}` - Soft-delete publisher
- `GET /publishers/{id}/books` - List publisher books

### Books
- `POST /books/` - Create book
- `GET /books/` - List books
- `GET /books/{id}` - Get book details
- `PUT /books/{id}` - Update book
- `DELETE /books/{id}` - Soft-delete book
- `POST /books/{id}/upload` - Upload book content

### Storage
- `GET /storage/publishers/{name}/` - List publisher assets
- `POST /storage/publishers/{name}/{type}/upload` - Upload assets
- `GET /storage/teachers/{id}/` - List teacher materials
- `POST /storage/teachers/{id}/{type}/upload` - Upload materials

### Trash
- `GET /trash/` - List trashed files
- `POST /trash/restore` - Restore file
- `DELETE /trash/{object_name}` - Permanently delete

## Technology Stack

- **React 18**: UI framework
- **TypeScript**: Type safety
- **Vite**: Build tool and dev server
- **TanStack Query**: Server state management
- **React Router**: Client-side routing
- **Tailwind CSS**: Styling
- **Shadcn/ui**: Component library

## File Structure

```
apps/admin-panel/
├── src/
│   ├── components/        # Reusable UI components
│   │   ├── NavBar.tsx    # Main navigation
│   │   ├── PublisherFormDialog.tsx
│   │   └── TeacherUploadDialog.tsx
│   ├── pages/            # Route components
│   │   ├── Books.tsx     # Book listing
│   │   ├── Publishers.tsx
│   │   ├── PublisherDetail.tsx
│   │   ├── Teachers.tsx
│   │   └── Trash.tsx
│   ├── lib/              # API client and utilities
│   │   ├── books.ts      # Book API functions
│   │   ├── publishers.ts # Publisher API functions
│   │   ├── teachers.ts   # Teacher API functions
│   │   └── storage.ts    # Storage API functions
│   └── App.tsx           # Main application component
├── package.json
├── tsconfig.json
├── vite.config.ts
└── README.md
```

## Common Tasks

### Adding a New Publisher

1. Navigate to Publishers page
2. Click "New Publisher" button
3. Fill in publisher details:
   - Name (lowercase, URL-safe)
   - Display name
   - Description (optional)
   - Contact email (optional)
4. Click "Create Publisher"

### Uploading a Book

1. Ensure publisher exists
2. Navigate to Books page
3. Click "Upload Book"
4. Select publisher
5. Choose ZIP file with book content
6. Confirm upload

Required ZIP structure:
```
book-name/
  config.json          # Book metadata
  pages/              # Page content
  assets/             # Images, audio, etc.
```

### Restoring Deleted Content

1. Navigate to Trash page
2. Find the deleted item
3. Click "Restore" button
4. Confirm restoration
5. Item returns to original location

## Troubleshooting

### Common Issues

**"Publisher not found" error**
- Verify publisher exists in system
- Check publisher name spelling
- Ensure publisher status is 'active'

**Upload fails with "Invalid ZIP" error**
- Verify ZIP structure matches required format
- Check config.json is valid JSON
- Ensure no corrupted files in ZIP

**"Unauthorized" errors**
- Session may have expired - refresh page
- Check API credentials
- Verify API URL is correct

**Books not showing for publisher**
- Check publisher_id is correctly set on book
- Verify book status is not 'archived'
- Refresh page to reload data

## Development Guidelines

### Adding New Features

1. Create API client function in `src/lib/`
2. Create or update page component in `src/pages/`
3. Add route in `App.tsx`
4. Add navigation link in `NavBar.tsx` if needed
5. Test with local API server

### Code Style

- Use TypeScript for type safety
- Follow React hooks best practices
- Use TanStack Query for server state
- Keep components focused and small
- Write self-documenting code

## Support

For issues or questions:
- Check API logs for backend errors
- Review browser console for client errors
- Verify network requests in dev tools
- Ensure API and admin panel versions are compatible

## License

This project is part of the Flow Central Storage system.
