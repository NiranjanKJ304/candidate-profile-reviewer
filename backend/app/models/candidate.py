"""Candidate: the person being evaluated."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.developer_profile import DeveloperProfile
    from app.models.evaluation import Evaluation
    from app.models.resume import Resume


class Candidate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "candidates"
    __table_args__ = (CheckConstraint("length(btrim(full_name)) > 0", name="full_name_not_blank"),)

    full_name: Mapped[str] = mapped_column(String(200))
    # Optional: a candidate may be created before contact details are known.
    # Stored lower-cased by the API schema; unique so one person is not duplicated.
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    phone: Mapped[str | None] = mapped_column(String(50))
    location: Mapped[str | None] = mapped_column(String(200))

    # Deleting a candidate removes all of their personal data (resumes, profiles,
    # evaluations): ORM cascade + ON DELETE CASCADE at the database level.
    resumes: Mapped[list[Resume]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", passive_deletes=True
    )
    developer_profiles: Mapped[list[DeveloperProfile]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", passive_deletes=True
    )
    evaluations: Mapped[list[Evaluation]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:  # never include personal data in repr/logs
        return f"<Candidate id={self.id}>"
