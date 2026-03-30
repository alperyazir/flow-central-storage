# **Epic 1: Core API & Project Foundation**

**Epic Goal**: This epic lays the groundwork for the entire Flow Central Storage system. It involves setting up the monorepo, initializing the Python/FastAPI backend project, establishing a CI/CD pipeline, and implementing the core API for creating, reading, and updating book metadata. Upon completion, we will have a deployable, secure, and testable API service, forming the foundational layer for all future features.

## **Story 1.1: Project Initialization & CI/CD**
**As a** developer, **I want** a configured monorepo with an initial FastAPI application and a basic CI/CD pipeline, **so that** I can start building and deploying the backend service efficiently.
**Acceptance Criteria:**
1.  A monorepo structure is created and initialized with Git.
2.  A new FastAPI application is created within the monorepo at `apps/api`.
3.  A health-check endpoint (e.g., `GET /`) is implemented that returns a success message.
4.  Database configuration for connecting to a local PostgreSQL instance is in place.
5.  A basic CI pipeline (e.g., using GitHub Actions) is configured to run tests and linting on every push.

## **Story 1.2: Book Metadata Model & Database Migration**
**As an** administrator, **I want** the system to have a defined data structure for book metadata, **so that** book information can be stored and retrieved consistently.
**Acceptance Criteria:**
1.  A database migration tool (e.g., Alembic) is integrated into the FastAPI project.
2.  A migration script is created that generates a `books` table in the PostgreSQL database.
3.  The `books` table includes all required metadata fields: `id`, `publisher`, `book_name`, `language`, `category`, `version`, `status`, `created_at`, and `updated_at`.
4.  A Pydantic model for the `Book` entity is created to ensure data validation within the API.

## **Story 1.3: User Authentication Endpoints**
**As an** administrator, **I want** to securely log in to the system, **so that** I can receive an access token for making authenticated API requests.
**Acceptance Criteria:**
1.  A `users` table is created in the database to store admin credentials securely (hashed passwords).
2.  An endpoint (e.g., `POST /auth/login`) is created that accepts admin credentials.
3.  Upon successful authentication, the endpoint returns a JWT access token.
4.  A command-line script is created to securely add the first admin user to the database.

## **Story 1.4: CRUD Endpoints for Book Metadata**
**As an** administrator, **I want** API endpoints to create, read, update, and list book metadata, **so that** I can manage the book catalog programmatically.
**Acceptance Criteria:**
1.  All endpoints in this story are protected and require a valid JWT for access.
2.  `POST /books`: Creates a new book metadata record in the database.
3.  `GET /books`: Returns a list of all book metadata records.
4.  `GET /books/{book_id}`: Retrieves the metadata for a single book.
5.  `PUT /books/{book_id}`: Updates the metadata for an existing book.

---
## **Story 1.5: API CORS Configuration for Admin Panel Access**
**As a** frontend developer, **I want** the API to permit cross-origin requests from approved admin panel origins, **so that** the React client can successfully authenticate without browser CORS errors.
**Acceptance Criteria:**
1. The FastAPI service enables CORS middleware that allows credentials and standard methods (`GET`, `POST`, `OPTIONS`, etc.) for configured origins.
2. Allowed origins are configurable via environment variable(s) with sensible defaults for local development (`http://localhost:5173`).
3. Preflight `OPTIONS` requests to authentication endpoints (e.g., `/auth/login`) succeed without returning 405 errors.
4. Documentation (README or env sample) lists the new configuration knob(s) and default values.
5. Automated tests cover at least one CORS preflight scenario, validating expected headers in the response.
