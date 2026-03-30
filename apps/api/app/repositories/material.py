"""Database access helpers for material entities."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.material import (
    TEXT_MATERIAL_TYPES,
    AIProcessingStatusEnum,
    Material,
)
from app.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class MaterialRepository(BaseRepository[Material]):
    """Repository for interacting with material records."""

    def __init__(self) -> None:
        super().__init__(model=Material)

    def list_by_teacher(
        self,
        session: Session,
        teacher_id: int,
        status: str | None = None,
        file_type: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Material]:
        """Return materials for a specific teacher with optional filters."""
        statement = select(Material).where(Material.teacher_id == teacher_id)

        if status:
            statement = statement.where(Material.status == status)

        if file_type:
            statement = statement.where(Material.file_type == file_type)

        statement = statement.order_by(Material.created_at.desc())
        statement = statement.offset(skip).limit(limit)
        return list(session.scalars(statement).all())

    def count_by_teacher(
        self,
        session: Session,
        teacher_id: int,
        status: str | None = None,
    ) -> int:
        """Count materials for a specific teacher."""
        statement = select(func.count()).select_from(Material).where(Material.teacher_id == teacher_id)
        if status:
            statement = statement.where(Material.status == status)
        return session.execute(statement).scalar() or 0

    def get_by_teacher_and_name(
        self,
        session: Session,
        teacher_id: int,
        material_name: str,
    ) -> Material | None:
        """Fetch a material by teacher ID and material name."""
        statement = select(Material).where(Material.teacher_id == teacher_id, Material.material_name == material_name)
        return session.scalars(statement).first()

    def get_storage_stats(
        self,
        session: Session,
        teacher_id: int,
    ) -> dict:
        """Get storage statistics for a teacher (2 queries: aggregates + by_type)."""
        # Query 1: All aggregates in one shot using conditional counts
        agg_stmt = select(
            func.count(Material.id).label("total_count"),
            func.coalesce(func.sum(Material.size), 0).label("total_size"),
            func.count(Material.id).filter(Material.file_type.in_(TEXT_MATERIAL_TYPES)).label("ai_processable"),
            func.count(Material.id)
            .filter(Material.ai_processing_status == AIProcessingStatusEnum.COMPLETED.value)
            .label("ai_processed"),
        ).where(Material.teacher_id == teacher_id, Material.status != "archived")
        agg = session.execute(agg_stmt).first()

        # Query 2: File type breakdown
        by_type_stmt = (
            select(
                Material.file_type,
                func.count().label("count"),
                func.coalesce(func.sum(Material.size), 0).label("size"),
            )
            .where(Material.teacher_id == teacher_id, Material.status != "archived")
            .group_by(Material.file_type)
        )
        by_type = {row.file_type: {"count": row.count, "size": row.size} for row in session.execute(by_type_stmt).all()}

        return {
            "total_size": int(agg.total_size) if agg else 0,
            "total_count": agg.total_count if agg else 0,
            "by_type": by_type,
            "ai_processable_count": agg.ai_processable if agg else 0,
            "ai_processed_count": agg.ai_processed if agg else 0,
        }

    def get_bulk_storage_stats(
        self,
        session: Session,
        teacher_ids: list[int],
    ) -> dict[int, dict]:
        """Get storage stats for multiple teachers in bulk (2 queries total).

        Returns:
            Dict mapping teacher_id → stats dict.
        """
        if not teacher_ids:
            return {}

        # Query 1: Aggregates per teacher
        agg_stmt = (
            select(
                Material.teacher_id,
                func.count(Material.id).label("total_count"),
                func.coalesce(func.sum(Material.size), 0).label("total_size"),
                func.count(Material.id).filter(Material.file_type.in_(TEXT_MATERIAL_TYPES)).label("ai_processable"),
                func.count(Material.id)
                .filter(Material.ai_processing_status == AIProcessingStatusEnum.COMPLETED.value)
                .label("ai_processed"),
            )
            .where(Material.teacher_id.in_(teacher_ids), Material.status != "archived")
            .group_by(Material.teacher_id)
        )
        agg_rows = {row.teacher_id: row for row in session.execute(agg_stmt).all()}

        # Query 2: File type breakdown per teacher
        by_type_stmt = (
            select(
                Material.teacher_id,
                Material.file_type,
                func.count().label("count"),
                func.coalesce(func.sum(Material.size), 0).label("size"),
            )
            .where(Material.teacher_id.in_(teacher_ids), Material.status != "archived")
            .group_by(Material.teacher_id, Material.file_type)
        )
        by_type_map: dict[int, dict] = {}
        for row in session.execute(by_type_stmt).all():
            by_type_map.setdefault(row.teacher_id, {})[row.file_type] = {"count": row.count, "size": row.size}

        empty_stats = {
            "total_size": 0,
            "total_count": 0,
            "by_type": {},
            "ai_processable_count": 0,
            "ai_processed_count": 0,
        }
        result = {}
        for tid in teacher_ids:
            agg = agg_rows.get(tid)
            if agg:
                result[tid] = {
                    "total_size": int(agg.total_size),
                    "total_count": agg.total_count,
                    "by_type": by_type_map.get(tid, {}),
                    "ai_processable_count": agg.ai_processable,
                    "ai_processed_count": agg.ai_processed,
                }
            else:
                result[tid] = dict(empty_stats)
        return result

    def list_pending_ai_processing(
        self,
        session: Session,
        teacher_id: int | None = None,
        limit: int = 100,
    ) -> list[Material]:
        """List materials that need AI processing."""
        statement = select(Material).where(
            Material.status != "archived",
            Material.file_type.in_(TEXT_MATERIAL_TYPES),
            Material.ai_processing_status.in_(
                [
                    AIProcessingStatusEnum.NOT_STARTED.value,
                    AIProcessingStatusEnum.FAILED.value,
                ]
            ),
        )

        if teacher_id is not None:
            statement = statement.where(Material.teacher_id == teacher_id)

        statement = statement.order_by(Material.created_at).limit(limit)
        return list(session.scalars(statement).all())

    def list_processing(
        self,
        session: Session,
        teacher_id: int | None = None,
    ) -> list[Material]:
        """List materials currently being processed."""
        statement = select(Material).where(
            Material.ai_processing_status.in_(
                [
                    AIProcessingStatusEnum.QUEUED.value,
                    AIProcessingStatusEnum.PROCESSING.value,
                ]
            )
        )

        if teacher_id is not None:
            statement = statement.where(Material.teacher_id == teacher_id)

        return list(session.scalars(statement).all())

    def create(self, session: Session, *, data: dict[str, object]) -> Material:
        """Create a new material record."""
        # Set AI processing status based on file type
        file_type = str(data.get("file_type", "")).lower()
        if file_type not in TEXT_MATERIAL_TYPES:
            data["ai_processing_status"] = AIProcessingStatusEnum.NOT_APPLICABLE.value

        material = Material(**data)
        created = self.add(session, material)
        session.commit()
        return created

    def update(self, session: Session, material: Material, *, data: dict[str, object]) -> Material:
        """Update an existing material."""
        for field, value in data.items():
            setattr(material, field, value)
        session.flush()
        session.refresh(material)
        session.commit()
        return material

    def update_ai_status(
        self,
        session: Session,
        material: Material,
        status: str,
        job_id: str | None = None,
    ) -> Material:
        """Update AI processing status for a material."""
        material.ai_processing_status = status
        if job_id is not None:
            material.ai_job_id = job_id
        if status == AIProcessingStatusEnum.COMPLETED.value:
            material.ai_processed_at = datetime.now(timezone.utc)
        session.flush()
        session.refresh(material)
        session.commit()
        return material

    def delete(self, session: Session, material: Material) -> None:
        """Permanently remove a material record from the database."""
        logger.info(
            f"Deleting material '{material.material_name}' (ID: {material.id}, Teacher ID: {material.teacher_id})"
        )
        session.delete(material)
        session.commit()
        logger.info(f"Successfully deleted material '{material.material_name}'")

    def soft_delete(self, session: Session, material: Material) -> Material:
        """Soft-delete a material by setting status to archived."""
        material.status = "archived"
        session.flush()
        session.refresh(material)
        session.commit()
        return material
