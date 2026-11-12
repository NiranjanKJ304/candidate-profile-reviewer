"""JobDescription: the role a candidate is evaluated against.

A JD can be uploaded as a file (PDF/DOCX/TXT). ``title`` and ``description_text``
are then unknown until extraction/structuring complete, so both are nullable
(migration 0003); when present they must not be blank.

Two structured versions are kept for provenance:

* ``analysis``       - the validated, grounded LLM output (never edited);
* ``reviewed_data``  - a human-corrected version (optional). When present it is
  the effective requirements set used by later phases.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import ExtractionMethod, ParseStatus, ResumeErrorCode
from app.db.base import Base, JsonDocument, TimestampMixin, UUIDPrimaryKeyMixin, string_enum

if TYPE_CHECKING:
    from app.models.evaluation import Evaluation


class JobDescription(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "job_descriptions"
    __table_args__ = (
        CheckConstraint("title IS NULL OR length(btrim(title)) > 0", name="title_not_blank"),
        CheckConstraint(
            "description_text IS NULL OR length(btrim(description_text)) > 0",
            name="description_not_blank",
        ),
        CheckConstraint(
            "file_size_bytes IS NULL OR file_size_bytes > 0", name="file_size_positive"
        ),
        CheckConstraint("sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'", name="sha256_hex"),
        CheckConstraint("page_count IS NULL OR page_count > 0", name="page_count_positive"),
    )

    title: Mapped[str | None] = mapped_column(String(300))
    company: Mapped[str | None] = mapped_column(String(200))
    # The full JD text: extracted text for uploads (set once extraction succeeds).
    description_text: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(2048))
    # Validated, grounded LLM output (ParsedJobDescription JSON).
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JsonDocument)

    # --- Phase 1B: uploaded file + parsing provenance (migration 0003) -------------
    original_filename: Mapped[str | None] = mapped_column(String(255))
    content_type: Mapped[str | None] = mapped_column(String(100))
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    # Unique: the same JD file is stored once; re-uploads return the existing record id.
    sha256: Mapped[str | None] = mapped_column(String(64), unique=True)
    storage_path: Mapped[str | None] = mapped_column(String(1024))
    extraction_method: Mapped[ExtractionMethod | None] = mapped_column(
        string_enum(ExtractionMethod, "extraction_method")
    )
    page_count: Mapped[int | None] = mapped_column(Integer)
    extracted_text: Mapped[str | None] = mapped_column(Text)
    parse_status: Mapped[ParseStatus] = mapped_column(
        string_enum(ParseStatus, "parse_status"),
        default=ParseStatus.PENDING,
        server_default=ParseStatus.PENDING.value,
    )
    parse_error_code: Mapped[ResumeErrorCode | None] = mapped_column(
        string_enum(ResumeErrorCode, "parse_error_code", length=40)
    )
    parse_error: Mapped[str | None] = mapped_column(Text)
    parse_warnings: Mapped[list[str] | None] = mapped_column(JsonDocument)
    parser_model: Mapped[str | None] = mapped_column(String(200))
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_data: Mapped[dict[str, Any] | None] = mapped_column(JsonDocument)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Evaluations keep a reference to the JD they were scored against, so a JD that
    # has evaluations cannot be deleted (ON DELETE RESTRICT) - history stays intact.
    evaluations: Mapped[list[Evaluation]] = relationship(
        back_populates="job_description", passive_deletes="all"
    )

    def __repr__(self) -> str:
        return f"<JobDescription id={self.id} status={self.parse_status}>"
