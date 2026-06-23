"""Pydantic schemas for application-wide settings."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# Default value for every known application setting. ``GET /settings`` merges
# stored overrides on top of these, so the response is always complete even
# before anything has been saved. Add new toggles here (and to the schemas
# below) — no migration needed thanks to the key/value table.
DEFAULT_SETTINGS: dict[str, object] = {
    "default_auto_bundle": True,
    "bundle_include_source_pdf": True,
}


class AppSettingsRead(BaseModel):
    """Full set of application settings (defaults merged with stored overrides)."""

    default_auto_bundle: bool = Field(
        default=True,
        description="Default state of the 'auto-create bundles after upload' checkbox.",
    )
    bundle_include_source_pdf: bool = Field(
        default=True,
        description="Include the source PDF (raw/original.pdf) in bundles. Off makes bundles ~2x smaller/faster.",
    )

    model_config = ConfigDict(from_attributes=True)


class AppSettingsUpdate(BaseModel):
    """Partial update payload; omitted fields are left unchanged."""

    default_auto_bundle: bool | None = Field(default=None)
    bundle_include_source_pdf: bool | None = Field(default=None)
