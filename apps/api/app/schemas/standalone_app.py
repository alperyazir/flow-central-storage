"""Pydantic schemas for standalone app templates and bundling."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class TemplateInfo(BaseModel):
    """Information about an uploaded standalone app template."""

    platform: str = Field(..., description="Platform identifier (mac, win, linux)")
    file_name: str = Field(..., description="Name of the uploaded template file")
    file_size: int = Field(..., description="Size of the template in bytes")
    uploaded_at: datetime = Field(..., description="Timestamp when template was uploaded")
    download_url: str = Field(..., description="Presigned URL to download the template")


class TemplateListResponse(BaseModel):
    """Response containing list of all uploaded templates."""

    templates: list[TemplateInfo] = Field(default_factory=list)


class TemplateUploadResponse(BaseModel):
    """Response after uploading a template."""

    platform: str
    file_name: str
    file_size: int
    message: str = "Template uploaded successfully"


class BundleRequest(BaseModel):
    """Request payload for creating a book bundle."""

    platform: Literal["mac", "win", "win7-8", "linux"] = Field(..., description="Target platform for the bundle")
    book_id: int = Field(..., description="ID of the book to bundle")
    force: bool = Field(
        default=False,
        description="If True, recreate bundle even if it already exists",
    )


class BundleResponse(BaseModel):
    """Response containing bundle download information."""

    download_url: str = Field(..., description="Presigned URL to download the bundle")
    file_name: str = Field(..., description="Name of the bundle file")
    file_size: int = Field(..., description="Size of the bundle in bytes")
    expires_at: datetime = Field(..., description="When the download URL expires")


class BundleInfo(BaseModel):
    """Information about a created bundle."""

    publisher_name: str = Field(..., description="Publisher name")
    book_name: str = Field(..., description="Book name")
    platform: str = Field(..., description="Platform identifier")
    file_name: str = Field(..., description="Bundle filename")
    file_size: int = Field(..., description="Size in bytes")
    created_at: datetime = Field(..., description="When bundle was created")
    object_name: str = Field(..., description="Full object path in storage")
    download_url: str | None = Field(None, description="Presigned download URL")


class BundleListResponse(BaseModel):
    """Response containing list of all bundles."""

    bundles: list[BundleInfo] = Field(default_factory=list)


class AsyncBundleRequest(BaseModel):
    """Request payload for creating a bundle asynchronously."""

    platform: Literal["mac", "win", "win7-8", "linux"] = Field(..., description="Target platform for the bundle")
    book_id: int = Field(..., description="ID of the book to bundle")
    force: bool = Field(
        default=False,
        description="If True, recreate bundle even if it already exists",
    )


class AsyncBundleResponse(BaseModel):
    """Response after queueing an async bundle creation."""

    job_id: str = Field(..., description="Unique job ID for tracking")
    status: str = Field(..., description="Current job status")
    message: str = Field(default="Bundle creation job queued")


class BundleJobStatus(BaseModel):
    """Status of a bundle creation job."""

    job_id: str = Field(..., description="Unique job ID")
    status: str = Field(..., description="Current status: queued, processing, completed, failed")
    progress: int = Field(default=0, description="Progress percentage 0-100")
    current_step: str = Field(default="", description="Current processing step")
    error_message: str | None = Field(None, description="Error message if failed")
    created_at: datetime = Field(..., description="When job was created")
    started_at: datetime | None = Field(None, description="When processing started")
    completed_at: datetime | None = Field(None, description="When job completed")
    platform: str | None = Field(None, description="Target platform")
    book_name: str | None = Field(None, description="Book name")
    book_id: str | None = Field(None, description="Book ID")


class BundleJobListResponse(BaseModel):
    """Response containing list of bundle jobs."""

    jobs: list[BundleJobStatus] = Field(default_factory=list)
    total: int = Field(default=0, description="Total number of jobs")


class BundleJobResult(BaseModel):
    """Result of a completed bundle creation job."""

    job_id: str = Field(..., description="Unique job ID")
    status: str = Field(..., description="Job status")
    progress: int = Field(default=100, description="Progress percentage")
    current_step: str = Field(default="", description="Final step")
    download_url: str | None = Field(None, description="Download URL if completed")
    file_name: str | None = Field(None, description="Bundle filename")
    file_size: int | None = Field(None, description="Bundle size in bytes")
    cached: bool = Field(default=False, description="Whether existing bundle was returned")
    error_message: str | None = Field(None, description="Error message if failed")
    created_at: datetime = Field(..., description="When job was created")
    completed_at: datetime | None = Field(None, description="When job completed")
