# Flow Central Storage Product Requirements Document (PRD)

## **Goals and Background Context**

### **Goals**

* Build a stable, scalable, and extensible storage and distribution system named **Flow Central Storage**.
* Manage both FlowBook application builds and the interactive book datasets within a single system.
* Support uploading book and application data as open-structure folders, not just zipped files.
* Provide a secure, JWT-authenticated API service to handle all data management operations like uploads, downloads, and listings.
* Enable direct streaming of audio and video files to the FlowBook app and future LMS.
* Implement a soft-delete mechanism where deleted files are moved to a trash bucket with a retention period before permanent deletion.
* Organize data by publisher to ensure proper separation and management.

### **Background Context**

FlowBook is a cross-platform desktop application that renders interactive books for students and teachers. The application is designed to be portable, with a clear separation between the app itself and the book datasets it loads.

This project's purpose is to create "Flow Central Storage," a unified and extensible platform to replace the current manual management of FlowBook builds and book data. This centralized system will provide a stable, scalable foundation for the entire ecosystem, supporting an Admin Panel for management and future integrations with a Learning Management System (LMS) and a Kanban production tracker.

### **Change Log**

| Date | Version | Description | Author |
| :--- | :--- | :--- | :--- |
| 2025-09-21 | 1.0 | Initial PRD draft created from Project Brief. | John (PM) |

---
## **Requirements**

### **Functional**

* **FR1**: The system must store both FlowBook application builds and book datasets in a MinIO S3-compatible object storage.
* **FR2**: The system must allow users to upload book and application data as complete folders, preserving their directory structure.
* **FR3**: The Admin Panel must provide functionality to upload, list, soft-delete, and restore files/folders.
* **FR4**: Soft-deleted items must be moved to a separate `trash` bucket.
* **FR5**: Book data must be organized by publisher name (e.g., `publisher_name/books/book1/`).
* **FR6**: A dedicated API service shall provide endpoints for all data management operations (upload, download, list, etc.).
* **FR7**: All operations that modify data must be logged to create an audit trail.
* **FR8**: The system must support direct streaming of audio and video files via HTTP range requests.
* **FR9**: The Admin Panel must allow for viewing and editing of book metadata stored in the database.

### **Non-Functional**

* **NFR1**: The API service must be stateless to allow for future horizontal scaling.
* **NFR2**: All API endpoints must be protected using JWT authentication.
* **NFR3**: Items in the `trash` bucket must be retained for a 7-day period before being eligible for permanent deletion.
* **NFR4**: A daily or weekly backup of the storage system must be synced to a secondary location.
* **NFR5**: System health (disk usage, network, errors) must be monitored, for example with Prometheus/Grafana.
* **NFR6**: Consistency between the file system and database metadata must be maintained by ensuring all operations are performed via the API.

---
## **User Interface Design Goals**

### **Overall UX Vision**

The UX vision for the Flow Central Storage Admin Panel is a clean, efficient, and straightforward web interface. The primary goal is to provide administrators with a powerful tool for managing application builds and book data with minimal friction. The design should prioritize clarity and ease of use over complex aesthetics, enabling users to perform core tasks like uploading, managing, and restoring content quickly and confidently.

### **Key Interaction Paradigms**

* **Dashboard-centric:** A central dashboard will serve as the main entry point, providing an at-a-glance overview and access to all key areas.
* **Table/List-based Data Display:** Books and applications will be presented in sortable and filterable tables or lists for easy navigation.
* **Modal-driven Actions:** Actions like editing metadata or confirming deletions will use modals to keep the user within their current context.
* **Direct Manipulation:** Users will interact directly with items, for example, by clicking a "delete" icon on a specific book entry.

### **Core Screens and Views**

* **Login Screen:** A secure page for administrator authentication.
* **Dashboard:** Main landing page showing lists of books and app builds, possibly with filtering by publisher.
* **Book Management View:** A detailed view for managing all books from a specific publisher.
* **App Build Management View:** A view for managing all application builds for each platform (macOS, Linux, Windows).
* **Metadata Edit Modal/Page:** A form for viewing and editing the metadata associated with a book.
* **Trash/Archive View:** A dedicated area to view soft-deleted items and restore them.

### **Accessibility: WCAG AA**

* **Assumption:** The interface will be designed to meet WCAG 2.1 AA standards to ensure it is usable by people with disabilities. This includes considerations for color contrast, keyboard navigation, and screen reader compatibility.

### **Branding**

* **Assumption:** Minimal branding will be applied. The focus will be on a clean, professional, and functional layout rather than a distinct brand identity at this stage.

### **Target Device and Platforms: Web Responsive**

* The application will be a responsive web interface, optimized for use on standard desktop and laptop screen sizes.

---
## **Technical Assumptions**

### **Repository Structure: Monorepo**

* A **monorepo** structure will be used to simplify shared code and dependency management between the API, Admin Panel, and future applications.

### **Service Architecture: Scalable Monolith**

* A **scalable (or modular) monolith** is recommended for the initial version to speed up development while allowing for future migration to microservices.

### **Testing Requirements: Unit + Integration**

* The project will include both **unit and integration tests** to ensure code quality and system reliability.

### **Additional Technical Assumptions and Requests**

* **Backend Stack**: **Python** with the **FastAPI** framework will be used for the API service.
* **Frontend Stack**: **React** (with Vite and TypeScript) is the chosen framework for the Admin Panel.
* **Storage**: The system will use **MinIO** for S3-compatible object storage.
* **Authentication**: **JWT** will be used for securing the API service.
* **Monitoring**: **Prometheus/Grafana** will be the target for system monitoring.
* **Database**: **PostgreSQL** is recommended for storing book metadata.

---
## **Epic List**

* **Epic 1: Core API & Project Foundation:** Establish the secure, authenticated API backbone with essential infrastructure, CI/CD, and basic book metadata management.
* **Epic 2: Storage Service Integration:** Implement the core file and folder management capabilities by integrating the API with the MinIO object storage.
* **Epic 3: Admin Panel MVP:** Develop the minimum viable React-based Admin Panel for administrators to log in, view, upload, and manage books and application builds.
* **Epic 4: Advanced Features & Production Readiness:** Implement the soft-delete/restore functionality and integrate the planned backup and monitoring solutions.

---
## **Epic 1: Core API & Project Foundation**

**Epic Goal**: This epic lays the groundwork for the entire Flow Central Storage system. It involves setting up the monorepo, initializing the Python/FastAPI backend project, establishing a CI/CD pipeline, and implementing the core API for creating, reading, and updating book metadata. Upon completion, we will have a deployable, secure, and testable API service, forming the foundational layer for all future features.

### **Story 1.1: Project Initialization & CI/CD**
**As a** developer, **I want** a configured monorepo with an initial FastAPI application and a basic CI/CD pipeline, **so that** I can start building and deploying the backend service efficiently.
**Acceptance Criteria:**
1.  A monorepo structure is created and initialized with Git.
2.  A new FastAPI application is created within the monorepo at `apps/api`.
3.  A health-check endpoint (e.g., `GET /`) is implemented that returns a success message.
4.  Database configuration for connecting to a local PostgreSQL instance is in place.
5.  A basic CI pipeline (e.g., using GitHub Actions) is configured to run tests and linting on every push.

### **Story 1.2: Book Metadata Model & Database Migration**
**As an** administrator, **I want** the system to have a defined data structure for book metadata, **so that** book information can be stored and retrieved consistently.
**Acceptance Criteria:**
1.  A database migration tool (e.g., Alembic) is integrated into the FastAPI project.
2.  A migration script is created that generates a `books` table in the PostgreSQL database.
3.  The `books` table includes all required metadata fields: `id`, `publisher`, `book_name`, `language`, `category`, `version`, `status`, `created_at`, and `updated_at`.
4.  A Pydantic model for the `Book` entity is created to ensure data validation within the API.

### **Story 1.3: User Authentication Endpoints**
**As an** administrator, **I want** to securely log in to the system, **so that** I can receive an access token for making authenticated API requests.
**Acceptance Criteria:**
1.  A `users` table is created in the database to store admin credentials securely (hashed passwords).
2.  An endpoint (e.g., `POST /auth/login`) is created that accepts admin credentials.
3.  Upon successful authentication, the endpoint returns a JWT access token.
4.  A command-line script is created to securely add the first admin user to the database.

### **Story 1.4: CRUD Endpoints for Book Metadata**
**As an** administrator, **I want** API endpoints to create, read, update, and list book metadata, **so that** I can manage the book catalog programmatically.
**Acceptance Criteria:**
1.  All endpoints in this story are protected and require a valid JWT for access.
2.  `POST /books`: Creates a new book metadata record in the database.
3.  `GET /books`: Returns a list of all book metadata records.
4.  `GET /books/{book_id}`: Retrieves the metadata for a single book.
5.  `PUT /books/{book_id}`: Updates the metadata for an existing book.

---
## **Epic 2: Storage Service Integration**
**Epic Goal**: This epic implements the core file and folder management capabilities by integrating the API with the MinIO object storage. It will deliver the functionality for uploading, preserving, and listing the directory structures for both book datasets and application builds. Upon completion, the system will be able to physically store and retrieve the content managed by the metadata API from Epic 1.

### **Story 2.1: MinIO Service Connection & Bucket Setup**
**As a** developer, **I want** the API to connect to the MinIO storage service and ensure the necessary buckets exist, **so that** the application is ready to handle file storage operations.
**Acceptance Criteria:**
1.  The FastAPI application securely connects to the MinIO instance using credentials from environment variables.
2.  A utility script or an application startup event ensures the required buckets (`books`, `apps`, `trash`) are created if they don't already exist.
3.  The connection is robust and includes basic error handling for connectivity issues.

### **Story 2.2: Book Folder Upload Endpoint**
**As an** administrator, **I want** an API endpoint that can upload an entire book folder, **so that** I can add new book datasets to the system.
**Acceptance Criteria:**
1.  A new endpoint (e.g., `POST /books/{book_id}/upload`) is created to handle folder uploads.
2.  The endpoint accepts a folder structure and recursively uploads all files and sub-folders to the `books` bucket in MinIO.
3.  Files are stored under a path corresponding to their publisher and book name (e.g., `publisher_name/book_name/`).
4.  The endpoint returns a success message with a manifest of the uploaded files.
5.  The endpoint is protected and requires a valid JWT.

### **Story 2.3: Application Build Folder Upload Endpoint**
**As an** administrator, **I want** an API endpoint that can upload an application build folder, **so that** I can add new FlowBook application versions to the system.
**Acceptance Criteria:**
1.  A new endpoint (e.g., `POST /apps/{platform}/upload`) is created for app build uploads.
2.  The endpoint accepts a folder and uploads its contents to the `apps` bucket in MinIO.
3.  Files are stored under a path corresponding to their platform (e.g., `macOS/`, `windows/`).
4.  The endpoint is protected and requires a valid JWT.

### **Story 2.4: List Contents of Buckets/Folders**
**As an** administrator, **I want** to list the contents of books and application builds via the API, **so that** I can verify uploads and manage stored files.
**Acceptance Criteria:**
1.  An endpoint (e.g., `GET /storage/books/{publisher}/{book_name}`) is created to list the file structure of a specific book.
2.  An endpoint (e.g., `GET /storage/apps/{platform}`) is created to list the contents of a specific application build.
3.  The endpoints return a structured list of files and folders (e.g., a JSON tree).
4.  The endpoints are protected and require a valid JWT.

---
## **Epic 3: Admin Panel MVP**
**Epic Goal**: This epic focuses on creating the user-facing interface for the Flow Central Storage system. We will develop a minimum viable Admin Panel using React that consumes the APIs built in the previous epics. The goal is to provide administrators with the essential tools to log in, view book and app data, and upload new content. Upon completion, we will have the first end-to-end, user-operable version of the platform.

### **Story 3.1: React App Initialization & Layout**
**As a** developer, **I want** a new React application initialized within the monorepo with basic routing and a main application layout, **so that** I have a foundation for building the Admin Panel UI.
**Acceptance Criteria:**
1.  A new React application (using Vite + TypeScript) is created in the monorepo at `apps/admin-panel`.
2.  A routing library (e.g., React Router) is set up with initial routes for Login and Dashboard pages.
3.  A main layout component is created that includes a persistent navigation bar and a content area for pages to render in.
4.  The React application is configured to communicate with the backend API using an environment variable for the API base URL.

### **Story 3.2: Admin Login Page & Authentication**
**As an** administrator, **I want** to log in through a user interface, **so that** I can securely access the Admin Panel.
**Acceptance Criteria:**
1.  A `/login` page is created with fields for an email/username and password.
2.  Submitting the form calls the `POST /auth/login` API endpoint.
3.  On successful login, the returned JWT is securely stored, and the user is redirected to the dashboard.
4.  An appropriate error message is displayed on the login page if authentication fails.
5.  A state management solution (e.g., Zustand, Redux Toolkit) is implemented to manage the user's authentication state globally.

### **Story 3.3: Dashboard for Listing Books & Apps**
**As an** administrator, **I want** to see lists of all available books and application builds on a dashboard, **so that** I can get an overview of the stored content.
**Acceptance Criteria:**
1.  The dashboard page is a protected route, redirecting unauthenticated users to the login page.
2.  On page load, the component calls the necessary API endpoints to fetch the list of books and app builds.
3.  The book and app data are displayed in clear, sortable tables.
4.  The book list table includes columns for key metadata like Title, Publisher, Language, and Category.
5.  The UI provides a way to filter the book list (e.g., by publisher).

### **Story 3.4: Book and App Folder Upload UI**
**As an** administrator, **I want** a user interface to upload new book and app build folders, **so that** I can add content to the system without using an API client.
**Acceptance Criteria:**
1.  A prominent "Upload" button is present on the dashboard.
2.  Clicking the button opens a modal or new page that allows the user to select a folder from their local machine.
3.  The interface provides clear instructions on what to upload (e.g., "Select a Book Data Folder").
4.  Once a folder is selected, the UI calls the correct API endpoint (e.g., `POST /books/{book_id}/upload`).
5.  The UI displays the upload progress and provides clear success or error feedback to the user upon completion.

---
## **Epic 4: Advanced Features & Production Readiness**
**Epic Goal**: This final epic elevates the MVP from a functional prototype to a robust, production-ready application. It focuses on implementing critical operational features mentioned in the brief, such as the soft-delete and restore mechanism, backups, and monitoring. Upon completion, the Flow Central Storage system will be fully operational, resilient, and ready for production use.

### **Story 4.1: Soft-Delete for Books and Apps**
**As an** administrator, **I want** to soft-delete books and app builds, **so that** I can remove them from the main view without permanently losing them immediately.
**Acceptance Criteria:**
1.  New API endpoints are created to handle soft-delete requests (e.g., `DELETE /books/{book_id}`).
2.  When an item is deleted, its corresponding folder in MinIO is moved from the `books` or `apps` bucket to the `trash` bucket.
3.  The path of the item within the `trash` bucket must be preserved to allow for restoration.
4.  For books, the metadata `status` field in the database is updated to `archived`.
5.  "Delete" buttons are added to the book and app lists in the Admin Panel UI, which trigger the soft-delete API call.

### **Story 4.2: Restore Functionality from Trash**
**As an** administrator, **I want** to view and restore soft-deleted items, **so that** I can recover from accidental deletions.
**Acceptance Criteria:**
1.  A new "Trash" page/view is created in the Admin Panel that lists all items in the `trash` bucket.
2.  Each item in the trash view has a "Restore" button.
3.  A new API endpoint (e.g., `POST /storage/restore`) is created to handle the restore logic.
4.  When an item is restored, its folder is moved from the `trash` bucket back to its original location in the `books` or `apps` bucket.
5.  For restored books, the metadata `status` is updated from `archived` back to `published`.

### **Story 4.3: Implement Automated Storage Backups**
**As a** developer, **I want** an automated daily backup of the MinIO storage, **so that** data can be recovered in case of a server failure.
**Acceptance Criteria:**
1.  A script is created that can sync all MinIO buckets to a secondary, off-site storage location.
2.  The script is configured to run automatically on a daily schedule via a cron job on the VPS.
3.  The script includes logging to provide a clear record of successful and failed backup attempts.

### **Story 4.4: Integrate Application Monitoring**
**As a** developer, **I want** to integrate the system with Prometheus/Grafana, **so that** I can monitor its health and performance.
**Acceptance Criteria:**
1.  The FastAPI application is configured to expose a `/metrics` endpoint compatible with Prometheus.
2.  The endpoint exposes key application metrics, such as request count, error rate, and request latency.
3.  A basic Grafana dashboard configuration is created to visualize the core metrics from the API.

---
## **Checklist Results Report**

* **Final Decision**: **READY FOR ARCHITECT**
* **Executive Summary**: The PRD is comprehensive, properly structured, and ready for architectural design. It successfully translates the business needs from the Project Brief into a complete and actionable set of requirements, epics, and stories. The MVP scope is clear and logical.
* **Critical Deficiencies**: None.

| Category | Status | Critical Issues |
| :--- | :--- | :--- |
| 1. Problem Definition & Context | ✅ PASS | None |
| 2. MVP Scope Definition | ✅ PASS | None |
| 3. User Experience Requirements | ✅ PASS | None |
| 4. Functional Requirements | ✅ PASS | None |
| 5. Non-Functional Requirements | ✅ PASS | None |
| 6. Epic & Story Structure | ✅ PASS | None |
| 7. Technical Guidance | ✅ PASS | None |
| 8. Cross-Functional Requirements | ✅ PASS | None |
| 9. Clarity & Communication | ✅ PASS | None |

---
## **Next Steps**

### **Architect Prompt**

This PRD is now complete. The next step is to engage the Architect to create the detailed technical architecture. Please provide the Architect with this document and the following prompt:

> "Please create the `fullstack-architecture.md` document based on the provided PRD. The architecture must adhere to the technical assumptions outlined within, including the use of a monorepo, a Python/FastAPI backend, a React frontend, a PostgreSQL database, and MinIO for storage. The design should provide a clear blueprint for the development agents to implement the stories defined in the epics."
