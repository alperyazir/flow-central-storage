# **Epic 4: Advanced Features & Production Readiness**
**Epic Goal**: This final epic elevates the MVP from a functional prototype to a robust, production-ready application. It focuses on implementing critical operational features mentioned in the brief, such as the soft-delete and restore mechanism, backups, and monitoring. Upon completion, the Flow Central Storage system will be fully operational, resilient, and ready for production use.

## **Story 4.1: Soft-Delete for Books and Apps**
**As an** administrator, **I want** to soft-delete books and app builds, **so that** I can remove them from the main view without permanently losing them immediately.
**Acceptance Criteria:**
1.  New API endpoints are created to handle soft-delete requests (e.g., `DELETE /books/{book_id}`).
2.  When an item is deleted, its corresponding folder in MinIO is moved from the `books` or `apps` bucket to the `trash` bucket.
3.  The path of the item within the `trash` bucket must be preserved to allow for restoration.
4.  For books, the metadata `status` field in the database is updated to `archived`.
5.  "Delete" buttons are added to the book and app lists in the Admin Panel UI, which trigger the soft-delete API call.

## **Story 4.2: Restore Functionality from Trash**
**As an** administrator, **I want** to view and restore soft-deleted items, **so that** I can recover from accidental deletions.
**Acceptance Criteria:**
1.  A new "Trash" page/view is created in the Admin Panel that lists all items in the `trash` bucket.
2.  Each item in the trash view has a "Restore" button.
3.  A new API endpoint (e.g., `POST /storage/restore`) is created to handle the restore logic.
4.  When an item is restored, its folder is moved from the `trash` bucket back to its original location in the `books` or `apps` bucket.
5.  For restored books, the metadata `status` is updated from `archived` back to `published`.

## **Story 4.3: Implement Automated Storage Backups**
**As a** developer, **I want** an automated daily backup of the MinIO storage, **so that** data can be recovered in case of a server failure.
**Acceptance Criteria:**
1.  A script is created that can sync all MinIO buckets to a secondary, off-site storage location.
2.  The script is configured to run automatically on a daily schedule via a cron job on the VPS.
3.  The script includes logging to provide a clear record of successful and failed backup attempts.

## **Story 4.4: Integrate Application Monitoring**
**As a** developer, **I want** to integrate the system with Prometheus/Grafana, **so that** I can monitor its health and performance.
**Acceptance Criteria:**
1.  The FastAPI application is configured to expose a `/metrics` endpoint compatible with Prometheus.
2.  The endpoint exposes key application metrics, such as request count, error rate, and request latency.
3.  A basic Grafana dashboard configuration is created to visualize the core metrics from the API.

---