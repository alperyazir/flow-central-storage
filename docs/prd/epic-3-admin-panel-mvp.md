# **Epic 3: Admin Panel MVP**
**Epic Goal**: This epic focuses on creating the user-facing interface for the Flow Central Storage system. We will develop a minimum viable Admin Panel using React that consumes the APIs built in the previous epics. The goal is to provide administrators with the essential tools to log in, view book and app data, and upload new content. Upon completion, we will have the first end-to-end, user-operable version of the platform.

## **Story 3.1: React App Initialization & Layout**
**As a** developer, **I want** a new React application initialized within the monorepo with basic routing and a main application layout, **so that** I have a foundation for building the Admin Panel UI.
**Acceptance Criteria:**
1.  A new React application (using Vite + TypeScript) is created in the monorepo at `apps/admin-panel`.
2.  A routing library (e.g., React Router) is set up with initial routes for Login and Dashboard pages.
3.  A main layout component is created that includes a persistent navigation bar and a content area for pages to render in.
4.  The React application is configured to communicate with the backend API using an environment variable for the API base URL.

## **Story 3.2: Admin Login Page & Authentication**
**As an** administrator, **I want** to log in through a user interface, **so that** I can securely access the Admin Panel.
**Acceptance Criteria:**
1.  A `/login` page is created with fields for an email/username and password.
2.  Submitting the form calls the `POST /auth/login` API endpoint.
3.  On successful login, the returned JWT is securely stored, and the user is redirected to the dashboard.
4.  An appropriate error message is displayed on the login page if authentication fails.
5.  A state management solution (e.g., Zustand, Redux Toolkit) is implemented to manage the user's authentication state globally.

## **Story 3.3: Dashboard for Listing Books & Apps**
**As an** administrator, **I want** to see lists of all available books and application builds on a dashboard, **so that** I can get an overview of the stored content.
**Acceptance Criteria:**
1.  The dashboard page is a protected route, redirecting unauthenticated users to the login page.
2.  On page load, the component calls the necessary API endpoints to fetch the list of books and app builds.
3.  The book and app data are displayed in clear, sortable tables.
4.  The book list table includes columns for key metadata like Title, Publisher, Language, and Category.
5.  The UI provides a way to filter the book list (e.g., by publisher).

## **Story 3.4: Book and App Folder Upload UI**
**As an** administrator, **I want** a user interface to upload new book and app build folders, **so that** I can add content to the system without using an API client.
**Acceptance Criteria:**
1.  A prominent "Upload" button is present on the dashboard.
2.  Clicking the button opens a modal or new page that allows the user to select a folder from their local machine.
3.  The interface provides clear instructions on what to upload (e.g., "Select a Book Data Folder").
4.  Once a folder is selected, the UI calls the correct API endpoint (e.g., `POST /books/{book_id}/upload`).
5.  The UI displays the upload progress and provides clear success or error feedback to the user upon completion.

---