# **Goals and Background Context**

## **Goals**

* Build a stable, scalable, and extensible storage and distribution system named **Flow Central Storage**.
* Manage both FlowBook application builds and the interactive book datasets within a single system.
* Support uploading book and application data as open-structure folders, not just zipped files.
* Provide a secure, JWT-authenticated API service to handle all data management operations like uploads, downloads, and listings.
* Enable direct streaming of audio and video files to the FlowBook app and future LMS.
* Implement a soft-delete mechanism where deleted files are moved to a trash bucket with a retention period before permanent deletion.
* Organize data by publisher to ensure proper separation and management.

## **Background Context**

FlowBook is a cross-platform desktop application that renders interactive books for students and teachers. The application is designed to be portable, with a clear separation between the app itself and the book datasets it loads.

This project's purpose is to create "Flow Central Storage," a unified and extensible platform to replace the current manual management of FlowBook builds and book data. This centralized system will provide a stable, scalable foundation for the entire ecosystem, supporting an Admin Panel for management and future integrations with a Learning Management System (LMS) and a Kanban production tracker.

## **Change Log**

| Date | Version | Description | Author |
| :--- | :--- | :--- | :--- |
| 2025-09-21 | 1.0 | Initial PRD draft created from Project Brief. | John (PM) |

---