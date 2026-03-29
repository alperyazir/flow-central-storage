# Epic 13 — MinIO to SeaweedFS Migration

**Status:** Approved
**Created:** 2026-03-29
**Priority:** High (MinIO in maintenance mode, unpatched CVEs)

---

## Epic Goal

Replace MinIO with SeaweedFS as the S3-compatible object storage backend, eliminating dependency on unmaintained software while preserving full API compatibility and zero application code changes.

---

## Epic Description

### Why This Epic

MinIO entered maintenance mode in November 2025. No new features, no community PRs, Docker images pulled, and CVE-2025-62506 was declined for patching. Running unpatched MinIO is a growing security and compliance liability.

### Why SeaweedFS

- **Apache 2.0 license** — no AGPL complications
- **Free under 25TB** — our use case fits comfortably
- **Full S3 API compatibility** — all operations we use are supported
- **Production proven** — Kubeflow, SmartMore (petabyte scale)
- **Active development** — regular releases, healthy community
- **Zero application code changes** — MinIO Python SDK works against any S3 endpoint

### Migration Strategy

The MinIO Python SDK (`minio` package) is a standard S3 client. SeaweedFS exposes an S3-compatible endpoint. By simply pointing the endpoint configuration to SeaweedFS, all existing code works without modification.

### Success Criteria

- All storage operations functional (upload, download, list, delete, presigned URLs)
- All existing data migrated
- Zero application code changes (only infrastructure config)
- Health endpoint verifies SeaweedFS connectivity
- All tests pass
- Processing pipeline works end-to-end

---

## Stories

### Story 13.1 — SeaweedFS Docker Compose Setup

**Goal:** Replace MinIO with SeaweedFS in docker-compose for local development.

**Scope:**
- Replace `minio/minio` service with SeaweedFS services (master, volume, filer, s3)
- Configure S3 gateway on port 8333 (or reuse 9000 for minimal config changes)
- Update `FCS_MINIO_ENDPOINT` default to SeaweedFS endpoint
- Verify bucket creation works via init script
- Update health check in docker-compose
- Keep MinIO console replacement: SeaweedFS has a built-in filer UI

**AC:**
1. `docker compose up` starts SeaweedFS instead of MinIO
2. S3 endpoint accessible and responds to bucket operations
3. Existing init script (`scripts/init_minio.py`) works or is adapted
4. Health endpoint reports storage as "ok"

---

### Story 13.2 — Data Migration Tooling

**Goal:** Provide tooling to migrate existing data from MinIO to SeaweedFS.

**Scope:**
- Create migration script using `rclone` or `mc mirror`
- Document migration steps for each bucket (publishers, apps, teachers, trash)
- Handle bucket creation in SeaweedFS before migration
- Verify data integrity post-migration (object count + spot-check sizes)
- Document rollback procedure (keep MinIO data until verified)

**AC:**
1. Migration script moves all buckets from MinIO to SeaweedFS
2. Object counts match pre/post migration
3. Rollback procedure documented
4. Can run migration with zero downtime (both services running simultaneously)

---

### Story 13.3 — Integration Verification & Cleanup

**Goal:** Verify all application flows work with SeaweedFS and clean up MinIO references.

**Scope:**
- Run full test suite against SeaweedFS backend
- Test: book upload → process AI → serve via API key
- Test: publisher asset upload/download/delete
- Test: teacher material upload/download
- Test: trash soft-delete → restore
- Test: presigned URL generation and access
- Test: bundle creation (fget/fput operations)
- Rename config variables: `FCS_MINIO_*` → `FCS_S3_*` (optional, for clarity)
- Update deployment runbook
- Update docker-compose.prod.yml

**AC:**
1. All existing tests pass
2. End-to-end upload → process → serve flow works
3. Presigned URLs work correctly
4. Bundle creation works
5. Production docker-compose updated
6. Deployment runbook updated

---

## Story Sequence

```
13.1 SeaweedFS Setup → 13.2 Data Migration → 13.3 Verification & Cleanup
```

All stories are sequential — each depends on the previous.

## Estimated Effort

- **Story 13.1:** 1-2 hours (docker-compose + config)
- **Story 13.2:** 1 hour (migration script + verification)
- **Story 13.3:** 2-3 hours (testing all flows + documentation)
- **Total:** ~4-6 hours

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| S3 API incompatibility on edge operations | Low | Medium | All our operations are standard S3; test thoroughly in 13.3 |
| Data corruption during migration | Low | High | Verify object counts; keep MinIO running until verified |
| Performance regression | Low | Low | SeaweedFS benchmarks comparable; monitor after switch |
| SeaweedFS also goes unmaintained | Very Low | High | Apache 2.0 allows forking; active community as of 2026 |

## Definition of Done

- [ ] MinIO fully replaced by SeaweedFS in development and production
- [ ] All data migrated and verified
- [ ] All tests pass
- [ ] End-to-end flow verified
- [ ] Documentation updated
