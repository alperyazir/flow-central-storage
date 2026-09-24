"""Pydantic schemas used by the FastAPI application."""

from .ai_data import (
    AudioUrlResponse,
    ModuleDetailResponse,
    ModuleListResponse,
    ModuleSummary,
    ProcessingMetadataResponse,
    StageResultResponse,
    VocabularyResponse,
    VocabularyWordAudio,
    VocabularyWordResponse,
)
from .api_key import ApiKeyCreate, ApiKeyCreated, ApiKeyListResponse, ApiKeyRead
from .asset import AssetFileInfo, AssetTypeInfo, PublisherAssetsResponse
from .auth import LoginRequest, SessionResponse, TokenResponse
from .book import BookBase, BookCreate, BookRead, BookUpdate
from .processing import (
    CleanupStatsResponse,
    ProcessingJobResponse,
    ProcessingStatusResponse,
    ProcessingTriggerRequest,
    QueueStatsResponse,
)
from .publisher import (
    PublisherBase,
    PublisherCreate,
    PublisherRead,
    PublisherUpdate,
    PublisherWithBooks,
)
from .standalone_app import (
    BundleRequest,
    BundleResponse,
    TemplateInfo,
    TemplateListResponse,
    TemplateUploadCompleteRequest,
    TemplateUploadResponse,
    TemplateUploadUrlRequest,
    TemplateUploadUrlResponse,
)
from .storage import RestoreRequest, RestoreResponse, TrashEntryRead
from .teacher import (
    FileTypeStats,
    MaterialCreate,
    MaterialListItem,
    MaterialListResponse,
    MaterialRead,
    MaterialUpdate,
    StorageStatsResponse,
    TeacherCreate,
    TeacherListItem,
    TeacherListResponse,
    TeacherRead,
    TeacherUpdate,
    TeacherWithMaterials,
)
from .webhook import (
    WebhookDeliveryLogRead,
    WebhookEventBookData,
    WebhookEventPayload,
    WebhookSubscriptionCreate,
    WebhookSubscriptionRead,
    WebhookSubscriptionUpdate,
)

__all__ = [
    # AI Data schemas
    "AudioUrlResponse",
    "ModuleDetailResponse",
    "ModuleListResponse",
    "ModuleSummary",
    "ProcessingMetadataResponse",
    "StageResultResponse",
    "VocabularyResponse",
    "VocabularyWordAudio",
    "VocabularyWordResponse",
    # API Key schemas
    "ApiKeyCreate",
    "ApiKeyCreated",
    "ApiKeyListResponse",
    "ApiKeyRead",
    # Asset schemas
    "AssetFileInfo",
    "AssetTypeInfo",
    "PublisherAssetsResponse",
    # Book schemas
    "BookBase",
    "BookCreate",
    "BookRead",
    "BookUpdate",
    # Material schemas
    "MaterialCreate",
    "MaterialListItem",
    "MaterialListResponse",
    "MaterialRead",
    "MaterialUpdate",
    # Processing schemas
    "CleanupStatsResponse",
    "ProcessingJobResponse",
    "ProcessingStatusResponse",
    "ProcessingTriggerRequest",
    "QueueStatsResponse",
    # Publisher schemas
    "PublisherBase",
    "PublisherCreate",
    "PublisherRead",
    "PublisherUpdate",
    "PublisherWithBooks",
    # Storage schemas
    "FileTypeStats",
    "RestoreRequest",
    "RestoreResponse",
    "StorageStatsResponse",
    "TrashEntryRead",
    # Teacher schemas
    "TeacherCreate",
    "TeacherListItem",
    "TeacherListResponse",
    "TeacherRead",
    "TeacherUpdate",
    "TeacherWithMaterials",
    # Auth schemas
    "LoginRequest",
    "TokenResponse",
    "SessionResponse",
    # Webhook schemas
    "WebhookSubscriptionCreate",
    "WebhookSubscriptionRead",
    "WebhookSubscriptionUpdate",
    "WebhookDeliveryLogRead",
    "WebhookEventPayload",
    "WebhookEventBookData",
    # Standalone app schemas
    "BundleRequest",
    "BundleResponse",
    "TemplateInfo",
    "TemplateListResponse",
    "TemplateUploadCompleteRequest",
    "TemplateUploadResponse",
    "TemplateUploadUrlRequest",
    "TemplateUploadUrlResponse",
]
