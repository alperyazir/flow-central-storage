# Running Flow Central Storage Locally with Docker Compose

This stack mirrors the production deployment topology described in the architecture documents. It provisions the API, PostgreSQL, MinIO, and an Nginx reverse proxy.

## Prerequisites

- Docker and Docker Compose
- Python 3.11+ (for local development outside containers)
- Node.js 20+ and npm (for Turborepo workspace management)

## Services

| Service   | Purpose                                    | Ports |
|-----------|--------------------------------------------|-------|
| api       | FastAPI backend (`apps/api`)               | 8000  |
| postgres  | PostgreSQL 16 data store                   | 5432  |
| minio     | S3-compatible object storage               | 9000 (API), 9090 (console) |
| nginx     | Reverse proxy routing `/` traffic to API   | 8080  |

## Usage

From the repository root:

```bash
cd infrastructure
docker compose up --build
```

The API becomes available at <http://localhost:8000>. Through Nginx it is reachable via <http://localhost:8080>.

To stop the stack:

```bash
docker compose down
```

Persistent volumes are defined for PostgreSQL and MinIO data. Remove them with:

```bash
docker compose down --volumes
```

Environment defaults live in `apps/api/.env.example`. Copy it to `.env` in the same directory to adjust credentials as needed.
