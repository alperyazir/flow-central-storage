# Epic 12 — Pre-Deployment Audit & Production Pipeline (DCS)

**Status:** Draft
**Created:** 2026-03-26
**Priority:** Critical (blocks production launch)

---

## Epic Goal

Systematically audit the Flow Central Storage (DCS) codebase for security vulnerabilities, performance bottlenecks, and reliability risks, then ensure the deployment pipeline is production-ready — covering the API, admin panel, MinIO storage, and book processing pipeline.

---

## Epic Description

### Existing System Context

- **Stack:** FastAPI + SQLAlchemy + PostgreSQL (API), React (admin panel)
- **Storage:** MinIO (S3-compatible object storage)
- **Infrastructure:** Docker Compose, Nginx, monitoring
- **Services:** Book processing pipeline, webhook delivery, AI data indexing, teacher materials
- **Auth:** JWT (email/password) + API keys (new feature)
- **Monorepo:** `apps/api/`, `apps/admin-panel/`, `infrastructure/`, `web-bundles/`

### Why This Epic

DCS is the backbone of the Flow Hubb ecosystem — it stores all book content, teacher materials, and AI-generated data. Before production launch alongside the LMS, DCS needs the same rigorous audit treatment.

### Success Criteria

- Zero critical/high security findings remaining
- API key auth system verified secure
- MinIO access policies properly scoped
- Book processing pipeline reliable under load
- Webhook delivery guaranteed
- Production deployment verified

---

## Stories

### Story 12.0 — Codebase Cleanup

**Goal:** Clean dead code, debug artifacts, and dev leftovers.

**Scope:**
- Remove console.log/print debug statements
- Remove TODO/HACK/FIXME comments
- Run linters (ruff, eslint) — zero errors
- Clean .gitignore, remove dev artifacts
- Format all code (black, prettier)

---

### Story 12.1 — Security Audit

**Goal:** Identify and document all security vulnerabilities.

**Scope:**
- **Auth system:** JWT + API key auth — verify all endpoints have proper guards
- **API key implementation:** Review key generation, hashing, validation, scoping
- **MinIO access:** Verify bucket policies, presigned URL scoping, no public access
- **Storage endpoints:** Path traversal checks on file access endpoints
- **Input validation:** All request schemas validated, file upload limits
- **Secrets management:** No hardcoded creds, .env properly gitignored
- **CORS & headers:** Security headers configured
- **Rate limiting:** All endpoints protected
- **Webhook security:** HMAC signature validation, secret management
- **Processing pipeline:** Can unauthorized users trigger book processing?

**Output:** `docs/qa/security-audit-report.md`

---

### Story 12.2 — Performance Audit

**Goal:** Identify performance bottlenecks for production scale.

**Scope:**
- **Database queries:** N+1 patterns, missing indexes, unpaginated results
- **MinIO operations:** Large file upload/download throughput, concurrent access
- **Book processing:** Processing pipeline throughput, memory usage during ZIP extraction
- **Caching:** Is anything cached? What should be?
- **Connection pooling:** PostgreSQL pool config
- **API response times:** Slowest endpoints profiled
- **Admin panel:** Bundle size, lazy loading

**Output:** `docs/qa/performance-audit-report.md`

---

### Story 12.3 — Reliability Audit

**Goal:** Find edge cases, error handling gaps, and data integrity risks.

**Scope:**
- **Error handling:** Silent exception swallowing, error info leakage
- **Processing failures:** What happens when book processing fails? Can it resume? Is state corrupted?
- **Webhook delivery:** Retry logic, dead letter handling, delivery guarantees
- **Data integrity:** Cascade deletes, orphan records, race conditions
- **File integrity:** What if a ZIP is corrupted? What if MinIO is down during upload?
- **API contracts:** Admin panel types match API responses

**Output:** `docs/qa/reliability-audit-report.md`

---

### Story 12.4 — Fix Critical & High Findings

**Goal:** Resolve all critical and high findings from audits.

**Scope:** Depends on audit results.

---

### Story 12.5 — Production Readiness Verification

**Goal:** Verify DCS is production-ready.

**Scope:**
- Health endpoint works
- Docker production config verified
- Backup/restore for PostgreSQL + MinIO
- Monitoring/alerting configured
- SSL/TLS verified
- End-to-end: upload book → process → serve to LMS

---

## Story Sequence

```
12.0 Cleanup → 12.1 Security ──┐
                12.2 Performance ├→ 12.4 Fix Findings → 12.5 Verification
                12.3 Reliability ┘
```

## Definition of Done

- [ ] All stories completed with acceptance criteria met
- [ ] Zero critical/high findings
- [ ] DCS serves LMS correctly end-to-end
- [ ] Production deployment verified
