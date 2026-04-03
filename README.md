# Flow Central Storage (FCS)

Centralized storage and content management service for the Flow Learn ecosystem. Manages book content, teacher materials, and media assets with S3-compatible object storage.

## Architecture

```
apps/
  api/             Python FastAPI backend (PostgreSQL, MinIO/R2, Redis)
  admin-panel/     React + TypeScript admin interface (Vite, Tailwind CSS)
infrastructure/    Docker and deployment configurations
web-bundles/       Standalone web application bundles
```

## Tech Stack

- **API**: FastAPI, SQLAlchemy, PostgreSQL 16, Redis 7, MinIO SDK
- **Admin Panel**: React 18, TypeScript, Vite, Tailwind CSS
- **Storage**: Cloudflare R2 (S3-compatible) for books, teacher materials, and app bundles
- **Processing**: Background workers for PDF text extraction and content processing
- **Proxy**: Nginx for static file serving, PgBouncer for connection pooling
- **Deployment**: Docker Compose, GitHub Actions CI/CD

## Core Features

- **Book Management**: Upload, organize, and serve interactive book content (pages, audio, activities)
- **Teacher Materials**: Per-teacher isolated storage with upload, download, streaming, and presigned URL access
- **Publisher Management**: Multi-publisher support with isolated storage namespaces
- **Presigned URLs**: Direct browser-to-R2 access for efficient media delivery
- **Streaming**: HTTP Range request support for audio/video seeking
- **Content Processing**: PDF text extraction, language detection for AI workflows
- **API Key Auth**: JWT and API key authentication for service-to-service communication

## Storage Buckets

| Bucket | Purpose |
|--------|---------|
| Publishers | Book content (pages, audio, config, activities) |
| Teachers | Teacher-uploaded materials (documents, images, audio, video) |
| Apps | Standalone web application bundles |
| Trash | Soft-deleted content with retention policy |

## Development

```bash
# API
cd apps/api
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8081

# Admin Panel
cd apps/admin-panel
npm install
npm run dev
```

## Production

```bash
docker compose -f docker-compose.prod.yml up -d
```

Requires `.env` file with database credentials, R2/MinIO access keys, and JWT secret.

## License

Proprietary - All rights reserved.
