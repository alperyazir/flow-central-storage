"""Database access helpers for teacher entities."""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.teacher import Teacher
from app.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class TeacherRepository(BaseRepository[Teacher]):
    """Repository for interacting with teacher records."""

    def __init__(self) -> None:
        super().__init__(model=Teacher)

    def list_all(self, session: Session) -> list[Teacher]:
        """Return all teachers."""
        statement = select(Teacher)
        return list(session.scalars(statement).all())

    def list_paginated(self, session: Session, skip: int = 0, limit: int = 100) -> list[Teacher]:
        """Return teachers with pagination."""
        statement = select(Teacher).offset(skip).limit(limit)
        return list(session.scalars(statement).all())

    def count(self, session: Session) -> int:
        """Return total count of teachers."""
        statement = select(func.count()).select_from(Teacher)
        return session.execute(statement).scalar() or 0

    def list_active(self, session: Session, skip: int = 0, limit: int = 100) -> list[Teacher]:
        """Return only active teachers with pagination."""
        statement = select(Teacher).where(Teacher.status == "active").offset(skip).limit(limit)
        return list(session.scalars(statement).all())

    def count_active(self, session: Session) -> int:
        """Return count of active teachers."""
        statement = select(func.count()).select_from(Teacher).where(Teacher.status == "active")
        return session.execute(statement).scalar() or 0

    def list_archived(self, session: Session, skip: int = 0, limit: int = 100) -> list[Teacher]:
        """Return archived (inactive) teachers with pagination."""
        statement = select(Teacher).where(Teacher.status == "inactive").offset(skip).limit(limit)
        return list(session.scalars(statement).all())

    def count_archived(self, session: Session) -> int:
        """Return count of archived teachers."""
        statement = select(func.count()).select_from(Teacher).where(Teacher.status == "inactive")
        return session.execute(statement).scalar() or 0

    def get_by_teacher_id(self, session: Session, teacher_id: str) -> Teacher | None:
        """Fetch a teacher by unique external teacher_id."""
        statement = select(Teacher).where(Teacher.teacher_id == teacher_id)
        result = session.execute(statement)
        return result.scalars().first()

    def get_or_create_by_teacher_id(self, session: Session, teacher_id: str, display_name: str | None = None) -> Teacher:
        """Get existing teacher by teacher_id or create a new one."""
        teacher = self.get_by_teacher_id(session, teacher_id)
        if teacher is not None:
            return teacher

        teacher = Teacher(teacher_id=teacher_id, display_name=display_name or teacher_id)
        return self.add(session, teacher)

    def get_with_materials(self, session: Session, teacher_id: int) -> Teacher | None:
        """Fetch a teacher with materials eager-loaded to avoid N+1 queries."""
        statement = select(Teacher).options(selectinload(Teacher.materials)).where(Teacher.id == teacher_id)
        return session.scalars(statement).first()

    def search(
        self,
        session: Session,
        query: str,
        status: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Teacher]:
        """Search teachers by teacher_id or display_name."""
        statement = select(Teacher)

        if query:
            search_pattern = f"%{query}%"
            statement = statement.where(
                (Teacher.teacher_id.ilike(search_pattern)) | (Teacher.display_name.ilike(search_pattern))
            )

        if status:
            statement = statement.where(Teacher.status == status)

        statement = statement.offset(skip).limit(limit)
        return list(session.scalars(statement).all())

    def create(self, session: Session, *, data: dict[str, object]) -> Teacher:
        """Create a new teacher record."""
        teacher = Teacher(**data)
        created = self.add(session, teacher)
        session.commit()
        return created

    def update(self, session: Session, teacher: Teacher, *, data: dict[str, object]) -> Teacher:
        """Update an existing teacher."""
        for field, value in data.items():
            setattr(teacher, field, value)
        session.flush()
        session.refresh(teacher)
        session.commit()
        return teacher

    def delete(self, session: Session, teacher: Teacher) -> None:
        """Permanently remove a teacher record from the database.

        This will also delete all materials associated with the teacher due to
        cascade delete configured on the relationship.
        """
        # Load materials to ensure cascade delete works properly
        session.refresh(teacher)
        material_count = len(teacher.materials)

        logger.info(
            f"Deleting teacher '{teacher.teacher_id}' (ID: {teacher.id}) and {material_count} associated materials"
        )

        # Delete teacher (cascade will delete all materials)
        session.delete(teacher)
        session.commit()

        logger.info(f"Successfully deleted teacher '{teacher.teacher_id}' and {material_count} materials")

    def get_ai_settings(self, teacher: Teacher) -> dict[str, object]:
        """Get AI processing settings for a teacher.

        Returns a dict with ai_auto_process_enabled, ai_processing_priority, ai_audio_languages.
        None values indicate "use global default".
        """
        return {
            "ai_auto_process_enabled": teacher.ai_auto_process_enabled,
            "ai_processing_priority": teacher.ai_processing_priority,
            "ai_audio_languages": teacher.ai_audio_languages,
        }

    def update_ai_settings(
        self,
        session: Session,
        teacher: Teacher,
        *,
        ai_auto_process_enabled: bool | None = None,
        ai_processing_priority: str | None = None,
        ai_audio_languages: str | None = None,
    ) -> Teacher:
        """Update AI processing settings for a teacher.

        Pass None to reset a setting to "use global default".
        """
        teacher.ai_auto_process_enabled = ai_auto_process_enabled
        teacher.ai_processing_priority = ai_processing_priority
        teacher.ai_audio_languages = ai_audio_languages
        session.flush()
        session.refresh(teacher)
        session.commit()
        return teacher
