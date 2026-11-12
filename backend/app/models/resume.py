"""Resume: an uploaded resume file and (later) its extracted structure.

Phase 0 defines storage metadata only; upload handling and parsing arrive later.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import ExtractionMethod, ParseStatus, ResumeErrorCode
from app.db.base import Base, JsonDocument, TimestampMixin, UUIDPrimaryKeyMixin, string_enum

if TYPE_CHECKING:
    from app.models.candidate import Candidate


class Resume(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "resumes"
    __table_args__ = (
        CheckConstraint("file_size_bytes > 0", name="file_size_positive"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_hex"),
        CheckConstraint("page_count IS NULL OR page_count > 0", name="page_count_positive"),
        # The same file uploaded twice for one candidate is a duplicate.
        UniqueConstraint("candidate_id", "sha256"),
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )
    original_filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    file_size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_path: Mapped[str | None] = mapped_column(String(1024))

    parse_status: Mapped[ParseStatus] = mapped_column(
        string_enum(ParseStatus, "parse_status"),
        default=ParseStatus.PENDING,
        server_default=ParseStatus.PENDING.value,
    )
    parsed_data: Mapped[dict[str, Any] | None] = mapped_column(JsonDocument)
    parse_error: Mapped[str | None] = mapped_column(Text)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- Phase 1A: extraction + structuring provenance (migration 0002) ----------
    extraction_method: Mapped[ExtractionMethod | None] = mapped_column(
        string_enum(ExtractionMethod, "extraction_method")
    )
    page_count: Mapped[int | None] = mapped_column(Integer)
    # Raw text kept so the resume can be re-structured without re-extraction and so
    # later phases can check claims against the source.
    extracted_text: Mapped[str | None] = mapped_column(Text)
    parser_model: Mapped[str | None] = mapped_column(String(200))
    parse_error_code: Mapped[ResumeErrorCode | None] = mapped_column(
        string_enum(ResumeErrorCode, "parse_error_code", length=40)
    )
    parse_warnings: Mapped[list[str] | None] = mapped_column(JsonDocument)

    candidate: Mapped[Candidate] = relationship(back_populates="resumes")

    def __repr__(self) -> str:
        return f"<Resume id={self.id} status={self.parse_status}>"
