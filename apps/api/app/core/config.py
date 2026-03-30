from functools import lru_cache

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ImportError:  # pragma: no cover - exercised only when dependency missing
    from pydantic import BaseModel, ConfigDict

    class BaseSettings(BaseModel):
        """Fallback BaseSettings that mirrors pydantic-settings behaviour for tests."""

        model_config = ConfigDict()

    def SettingsConfigDict(**kwargs: object) -> ConfigDict:
        """Provide a ConfigDict-compatible factory when pydantic-settings is unavailable."""

        return ConfigDict(**kwargs)


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    app_name: str = "Flow Central Storage API"
    app_version: str = "0.1.0"

    database_scheme: str = "postgresql+psycopg"
    database_host: str = "pgbouncer"
    database_port: int = 6432
    database_user: str = "flow_admin"
    database_password: str = "flow_password"
    database_name: str = "flow_central"

    minio_endpoint: str = "seaweedfs-s3:8333"
    minio_external_url: str = "http://localhost:8333"  # Public URL for presigned URLs
    minio_access_key: str = "admin"
    minio_secret_key: str = "admin"
    minio_secure: bool = False
    minio_publishers_bucket: str = "publishers"
    minio_apps_bucket: str = "apps"
    minio_trash_bucket: str = "trash"
    minio_teachers_bucket: str = "teachers"
    trash_retention_days: int = 7

    # Teacher storage configuration
    teacher_quota_bytes: int = 524288000  # 500MB default
    teacher_max_file_size_bytes: int = 104857600  # 100MB default

    jwt_secret_key: str = "CHANGE_ME"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expires_minutes: int = 10080  # 7 days

    cors_allowed_origins: str | list[str] = "http://localhost:5173,http://localhost:5174"

    # LLM Provider Configuration
    deepseek_api_key: str = ""
    gemini_api_key: str = ""
    llm_primary_provider: str = "gemini"
    llm_fallback_provider: str = "deepseek"
    llm_default_model: str = "gemini-2.5-flash"
    llm_max_tokens: int = 4096
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 3

    # TTS Provider Configuration
    azure_tts_key: str = ""
    azure_tts_region: str = "eastus"
    tts_primary_provider: str = "edge"
    tts_fallback_provider: str = "azure"
    tts_default_voice_en: str = "en-US-JennyNeural"
    tts_default_voice_tr: str = "tr-TR-EmelNeural"
    tts_audio_format: str = "mp3"
    tts_timeout_seconds: int = 30
    tts_max_retries: int = 3
    tts_batch_concurrency: int = 5

    # Queue Configuration (Redis/arq)
    redis_url: str = "redis://localhost:6379"
    queue_name: str = "ai_processing"
    queue_max_concurrency: int = 3
    queue_max_retries: int = 3
    queue_job_timeout_seconds: int = 3600  # 1 hour
    queue_retry_delay_seconds: int = 60
    queue_default_priority: str = "normal"
    queue_job_ttl_seconds: int = 86400 * 7  # 7 days

    # PDF Extraction Configuration
    pdf_min_text_threshold: int = 50  # chars below this = scanned page
    pdf_min_word_threshold: int = 10  # words below this = scanned page
    pdf_ocr_enabled: bool = True  # enable OCR fallback for scanned pages
    pdf_ocr_batch_size: int = 5  # pages to OCR concurrently
    pdf_ocr_dpi: int = 150  # resolution for page image rendering
    pdf_max_pages: int = 500  # max pages per book

    # Segmentation Configuration
    segmentation_min_module_pages: int = 3  # min pages per module
    segmentation_max_module_pages: int = 30  # max pages per module before warning
    segmentation_max_modules: int = 50  # max modules per book
    segmentation_ai_enabled: bool = True  # enable AI-assisted segmentation
    segmentation_ai_fallback_on_poor_quality: bool = True  # use AI if header/TOC poor
    segmentation_default_strategy: str = "auto"  # auto, manual, header, toc, ai

    # Topic Analysis Configuration
    topic_analysis_max_topics: int = 5  # max topics to extract per module
    topic_analysis_max_grammar_points: int = 10  # max grammar points per module
    topic_analysis_temperature: float = 0.3  # LLM temperature for analysis
    topic_analysis_max_text_length: int = 8000  # max chars to send to LLM

    # Vocabulary Extraction Configuration
    vocabulary_max_words_per_module: int = 200  # max vocabulary words per module
    vocabulary_min_word_length: int = 3  # min word length to include
    vocabulary_temperature: float = 0.3  # LLM temperature for extraction
    vocabulary_max_text_length: int = 8000  # max chars to send to LLM

    # Audio Generation Configuration
    audio_generation_concurrency: int = 5  # concurrent TTS requests for batch
    audio_generation_languages: str = "en"  # languages to generate audio for
    audio_retry_failed: bool = True  # retry failed audio generation

    # Auto-Processing Configuration
    ai_auto_process_on_upload: bool = False  # trigger AI processing on book upload
    ai_auto_process_skip_existing: bool = True  # skip if book already processed

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="FCS_",
        extra="ignore",
    )

    @property
    def database_url(self) -> str:
        """Assemble a SQLAlchemy compatible database URL."""
        return (
            f"{self.database_scheme}://{self.database_user}:{self.database_password}"
            f"@{self.database_host}:{self.database_port}/{self.database_name}"
        )

    @property
    def minio_buckets(self) -> list[str]:
        """Return the list of buckets the application requires."""

        return [
            self.minio_publishers_bucket,
            self.minio_apps_bucket,
            self.minio_trash_bucket,
            self.minio_teachers_bucket,
        ]

    @property
    def teacher_allowed_mime_types(self) -> dict[str, list[str]]:
        """Return allowed MIME types by category for teacher uploads."""
        return {
            "documents": [
                "application/pdf",
                "text/plain",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ],
            "images": [
                "image/jpeg",
                "image/png",
                "image/gif",
                "image/webp",
            ],
            "audio": [
                "audio/mpeg",
                "audio/wav",
                "audio/ogg",
                "audio/mp4",
            ],
            "video": [
                "video/mp4",
                "video/webm",
                "video/quicktime",
            ],
        }

    @property
    def teacher_all_allowed_mime_types(self) -> list[str]:
        """Return flat list of all allowed MIME types for teacher uploads."""
        return [mime for mimes in self.teacher_allowed_mime_types.values() for mime in mimes]

    @property
    def resolved_cors_allowed_origins(self) -> list[str]:
        """Return the configured CORS origins as a normalized list."""

        if isinstance(self.cors_allowed_origins, str):
            return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

        return list(self.cors_allowed_origins)


@lru_cache
def get_settings() -> Settings:
    """Cache settings to avoid re-parsing environment files."""
    return Settings()
