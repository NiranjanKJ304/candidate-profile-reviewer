"""Evaluation: one assessment of a candidate against a job description.

The scoring workflow arrives in a later phase; Phase 0 defines the record and its
provenance fields (which resume and which model produced the result).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import EvaluationStatus
from app.db.base import Base, JsonDocument, TimestampMixin, UUIDPrimaryKeyMixin, string_enum

if TYPE_CHECKING:
    from app.models.candidate import Candidate
    from app.models.job_description import JobDescription
    from app.models.resume import Resume


class Evaluation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "evaluations"
    __table_args__ = (
        CheckConstraint(
            "overall_score IS NULL OR (overall_score >= 0 AND overall_score <= 100)",
            name="overall_score_range",
        ),
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )
    job_description_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job_descriptions.id", ondelete="RESTRICT"), index=True
    )
    # Resume the evaluation was based on; kept nullable so deleting an old resume
    # does not destroy the evaluation history.
    resume_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resumes.id", ondelete="SET NULL"), index=True
    )

    status: Mapped[EvaluationStatus] = mapped_column(
        string_enum(EvaluationStatus, "evaluation_status"),
        default=EvaluationStatus.PENDING,
        server_default=EvaluationStatus.PENDING.value,
    )
    # NULL = not scored yet; never conflated with a score of 0.
    overall_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    result: Mapped[dict[str, Any] | None] = mapped_column(JsonDocument)
    model_name: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    candidate: Mapped[Candidate] = relationship(back_populates="evaluations")
    job_description: Mapped[JobDescription] = relationship(back_populates="evaluations")
    resume: Mapped[Resume | None] = relationship()

    def __repr__(self) -> str:
        return f"<Evaluation id={self.id} status={self.status}>"
