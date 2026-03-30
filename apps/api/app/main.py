import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.db import SessionLocal
from app.monitoring import MetricsMiddleware
from app.monitoring import router as monitoring_router
from app.repositories.user import UserRepository
from app.routers import (
    ai_content,
    ai_data,
    api_keys,
    apps,
    auth,
    books,
    health,
    processing,
    publishers,
    standalone_apps,
    storage,
    teachers,
    teachers_crud,
    webhooks,
)
from app.scripts.create_admin import create_admin_user
from app.services import ensure_buckets, get_minio_client

logger = logging.getLogger(__name__)
settings = get_settings()

MINIO_MAX_ATTEMPTS = 5
MINIO_INITIAL_DELAY_SECONDS = 1.0


async def wait_for_minio() -> None:
    """Ensure MinIO buckets exist, retrying while the service starts up."""

    delay = MINIO_INITIAL_DELAY_SECONDS
    for attempt in range(1, MINIO_MAX_ATTEMPTS + 1):
        client = get_minio_client(settings)
        try:
            ensure_buckets(client, settings.minio_buckets)
            if attempt > 1:
                logger.info("Connected to MinIO after %d attempts", attempt)
            return
        except Exception as exc:  # pragma: no cover - network/service dependent
            if attempt == MINIO_MAX_ATTEMPTS:
                logger.error(
                    "Failed to connect to MinIO after %d attempts: %s",
                    attempt,
                    exc,
                )
                raise
            logger.warning(
                "MinIO not ready (attempt %d/%d): %s",
                attempt,
                MINIO_MAX_ATTEMPTS,
                exc,
            )
            await asyncio.sleep(delay)
            delay *= 2


def ensure_default_admin() -> None:
    """Create default admin user if no users exist."""

    with SessionLocal() as session:
        repository = UserRepository()
        # Check if any users exist
        existing_user = session.query(repository.model).first()

        if existing_user is None:
            # No users exist, create default admin
            default_email = "admin@admin.com"
            default_password = "admin"

            try:
                user = create_admin_user(session, email=default_email, password=default_password)
                logger.info(
                    "Created default admin user (email: %s, id: %d)",
                    default_email,
                    user.id,
                )
            except Exception as exc:
                logger.error("Failed to create default admin user: %s", exc)
        else:
            logger.info("Users already exist, skipping default admin creation")


def _validate_startup_config() -> None:
    """Check critical configuration at startup."""
    if settings.jwt_secret_key == "CHANGE_ME":
        raise RuntimeError(
            "SEC-C1: jwt_secret_key is still the default 'CHANGE_ME'. "
            "Set the FCS_JWT_SECRET_KEY environment variable to a secure random value."
        )
    if settings.database_password == "flow_password":
        logger.warning(
            "SEC-C5: database_password is still the default 'flow_password'. "
            "Set FCS_DATABASE_PASSWORD to a strong password before deploying to production."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _validate_startup_config()
    await wait_for_minio()
    ensure_default_admin()
    yield


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)

app.add_middleware(MetricsMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.resolved_cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)

app.include_router(auth.router)
app.include_router(api_keys.router)
app.include_router(books.router)
app.include_router(processing.router)
app.include_router(processing.dashboard_router)
app.include_router(ai_content.router)
app.include_router(ai_data.router)
app.include_router(publishers.router)
app.include_router(apps.router)
app.include_router(standalone_apps.router)
app.include_router(storage.router)
app.include_router(teachers.router)
app.include_router(teachers_crud.router)
app.include_router(webhooks.router)
app.include_router(health.router, prefix="/health")
app.include_router(monitoring_router)


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
