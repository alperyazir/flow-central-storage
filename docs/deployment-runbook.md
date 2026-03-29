# FCS Production Deployment Runbook

## Prerequisites

- Docker + Docker Compose installed
- Domain name configured with DNS pointing to server
- SSL certificate (Let's Encrypt or manual)

---

## 1. Environment Setup

### Required Environment Variables

Create a `.env` file in the project root (or set via your deployment platform):

```bash
# Database
POSTGRES_PASSWORD=<strong-random-password>
DCS_DATABASE_USER=dream_admin
DCS_DATABASE_PASSWORD=<strong-random-password>
DCS_DATABASE_HOST=postgres
DCS_DATABASE_PORT=5432
DCS_DATABASE_NAME=dream_central

# MinIO
MINIO_ROOT_USER=<minio-admin-user>
MINIO_ROOT_PASSWORD=<strong-random-password>
DCS_MINIO_ENDPOINT=minio:9000
DCS_MINIO_ACCESS_KEY=<same-as-MINIO_ROOT_USER>
DCS_MINIO_SECRET_KEY=<same-as-MINIO_ROOT_PASSWORD>

# Auth
DCS_JWT_SECRET_KEY=<random-64-char-string>

# CORS (production frontend URL)
DCS_CORS_ALLOWED_ORIGINS=https://admin.yourdomain.com

# Redis
DCS_REDIS_URL=redis://redis:6379

# LLM Providers
DCS_DEEPSEEK_API_KEY=<key>
DCS_GEMINI_API_KEY=<key>

# TTS
DCS_AZURE_TTS_KEY=<key>
DCS_AZURE_TTS_REGION=<region>
```

Generate secrets:
```bash
openssl rand -hex 32  # For JWT_SECRET_KEY
openssl rand -hex 16  # For passwords
```

---

## 2. Deploy Services

### Start order matters:

```bash
cd infrastructure

# 1. Start data stores first
docker compose up -d postgres redis seaweedfs-master seaweedfs-volume seaweedfs-filer seaweedfs-s3

# 2. Wait for healthy
docker compose exec postgres pg_isready -U postgres
docker compose exec redis redis-cli ping
# MinIO: check http://localhost:9090

# 3. Run database migrations
docker compose run --rm api alembic upgrade head

# 4. Start API + worker
docker compose up -d api worker

# 5. Start reverse proxy
docker compose up -d nginx

# 6. Start admin panel (production build)
docker compose --profile prod up -d admin-panel

# 7. (Optional) Start monitoring
docker compose --profile monitoring up -d prometheus grafana
```

### Verify health:
```bash
curl http://localhost:8081/health
# Expected: {"status":"healthy","checks":{"db":"ok","redis":"ok","minio":"ok"}}
```

---

## 3. SSL/TLS Configuration

### Option A: Let's Encrypt (recommended)

Add certbot to docker-compose or use a reverse proxy like Traefik/Caddy.

### Option B: Manual nginx SSL

Update `infrastructure/nginx/nginx.conf`:
```nginx
server {
    listen 443 ssl;
    server_name api.yourdomain.com;

    ssl_certificate /etc/nginx/ssl/cert.pem;
    ssl_certificate_key /etc/nginx/ssl/key.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    # Security headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;

    location / {
        proxy_pass http://api:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name api.yourdomain.com;
    return 301 https://$host$request_uri;
}
```

---

## 4. Database Backup & Restore

### Backup
```bash
# Automated daily backup
docker compose exec postgres pg_dump -U postgres dream_central > backup_$(date +%Y%m%d).sql

# Compressed
docker compose exec postgres pg_dump -U postgres dream_central | gzip > backup_$(date +%Y%m%d).sql.gz
```

### Restore
```bash
# From SQL file
docker compose exec -T postgres psql -U postgres dream_central < backup_20260326.sql

# From compressed
gunzip -c backup_20260326.sql.gz | docker compose exec -T postgres psql -U postgres dream_central
```

### Scheduled backups (cron)
```bash
0 2 * * * cd /path/to/project/infrastructure && docker compose exec -T postgres pg_dump -U postgres dream_central | gzip > /backups/dcs_$(date +\%Y\%m\%d).sql.gz
```

---

## 5. SeaweedFS Storage Backup & Restore

### Backup (using rclone)
```bash
# Configure rclone remote
rclone config create fcs s3 provider=Other \
  endpoint=http://localhost:8333 \
  access_key_id=admin secret_access_key=admin

# Mirror all buckets to local backup
for bucket in publishers apps teachers trash; do
  rclone sync fcs:$bucket /backups/seaweedfs/$bucket --progress
done
```

### Backup (volume-level)
```bash
docker compose stop seaweedfs-master seaweedfs-volume seaweedfs-filer seaweedfs-s3
# Copy Docker volumes
docker run --rm -v seaweedfs_master_data:/data -v /backups:/backup alpine tar czf /backup/seaweedfs_master_$(date +%Y%m%d).tar.gz /data
docker run --rm -v seaweedfs_volume_data:/data -v /backups:/backup alpine tar czf /backup/seaweedfs_volume_$(date +%Y%m%d).tar.gz /data
docker compose up -d seaweedfs-master seaweedfs-volume seaweedfs-filer seaweedfs-s3
```

### Restore
```bash
# From rclone backup
for bucket in publishers apps teachers trash; do
  rclone sync /backups/seaweedfs/$bucket fcs:$bucket --progress
done
```

### Migration from MinIO
```bash
# If migrating from an existing MinIO instance:
./infrastructure/scripts/migrate-minio-to-seaweedfs.sh
```

---

## 6. Monitoring

### Access
- **Prometheus:** http://localhost:9091
- **Grafana:** http://localhost:3000 (admin/admin)

### Key metrics to watch
- API response times (p50, p95, p99)
- Processing queue depth
- SeaweedFS storage usage
- Database connection pool utilization

---

## 7. End-to-End Verification

```bash
# 1. Login
TOKEN=$(curl -s -X POST http://localhost:8081/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@admin.com","password":"admin"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 2. Create API key for LMS
API_KEY=$(curl -s -X POST http://localhost:8081/api-keys/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"E2E Test"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['key'])")

# 3. List books (via API key)
curl -s http://localhost:8081/books/ \
  -H "Authorization: Bearer $API_KEY" | python3 -m json.tool

# 4. List publishers (via API key)
curl -s http://localhost:8081/publishers/ \
  -H "Authorization: Bearer $API_KEY" | python3 -m json.tool

# 5. Health check
curl -s http://localhost:8081/health | python3 -m json.tool
```

---

## 8. Rollback Procedure

```bash
# 1. Stop services
docker compose down

# 2. Restore previous images
docker compose pull  # or tag specific versions

# 3. Restore database
gunzip -c /backups/dcs_YYYYMMDD.sql.gz | docker compose exec -T postgres psql -U postgres dream_central

# 4. Restart
docker compose up -d postgres redis seaweedfs-master seaweedfs-volume seaweedfs-filer seaweedfs-s3
docker compose run --rm api alembic upgrade head
docker compose up -d api worker nginx
```

---

## 9. Troubleshooting

| Symptom | Check |
|---------|-------|
| Health returns "degraded" | Check individual service status in health response |
| API returns 500 | Check API logs: `docker compose logs api` |
| Processing stuck | Check worker logs: `docker compose logs worker` |
| Storage unreachable | Check SeaweedFS logs: `docker compose logs seaweedfs-s3 seaweedfs-filer` |
| DB connection errors | Check pool config and postgres max_connections |
