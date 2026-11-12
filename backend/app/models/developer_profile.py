"""DeveloperProfile: a candidate's account on an external developer platform.

Collection itself arrives in later phases. The columns already separate *ownership*
(is this really the candidate's account?) from *collection* (did we obtain data?),
and record provenance (source URL + collection time) for every snapshot.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import (
    CollectionErrorCode,
    CollectionStatus,
    OwnershipStatus,
    Platform,
    SyncStatus,
)
from app.db.base import Base, JsonDocument, TimestampMixin, UUIDPrimaryKeyMixin, string_enum

if TYPE_CHECKING:
    from app.models.candidate import Candidate


class DeveloperProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "developer_profiles"
    __table_args__ = (
        CheckConstraint("length(btrim(username)) > 0", name="username_not_blank"),
        # One account per platform per candidate.
        UniqueConstraint("candidate_id", "platform"),
        # Same platform account linked to several candidates is reported, not blocked.
        Index("ix_developer_profiles_platform_external_id", "platform", "external_id"),
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )
    platform: Mapped[Platform] = mapped_column(string_enum(Platform, "platform"))
    username: Mapped[str] = mapped_column(String(100))
    profile_url: Mapped[str | None] = mapped_column(String(2048))

    ownership_status: Mapped[OwnershipStatus] = mapped_column(
        string_enum(OwnershipStatus, "ownership_status"),
        default=OwnershipStatus.UNVERIFIED,
        server_default=OwnershipStatus.UNVERIFIED.value,
    )
    collection_status: Mapped[CollectionStatus] = mapped_column(
        string_enum(CollectionStatus, "collection_status"),
        default=CollectionStatus.NOT_COLLECTED,
        server_default=CollectionStatus.NOT_COLLECTED.value,
    )
    collected_data: Mapped[dict[str, Any] | None] = mapped_column(JsonDocument)
    # Provenance of the last collection.
    source_url: Mapped[str | None] = mapped_column(String(2048))
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- Phase 2: synchronisation lifecycle (migration 0004) ----------------------
    # Platform's stable account id (GitHub numeric id): survives username renames.
    external_id: Mapped[str | None] = mapped_column(String(64))
    sync_status: Mapped[SyncStatus] = mapped_column(
        string_enum(SyncStatus, "sync_status"),
        default=SyncStatus.PENDING,
        server_default=SyncStatus.PENDING.value,
    )
    sync_error_code: Mapped[CollectionErrorCode | None] = mapped_column(
        string_enum(CollectionErrorCode, "sync_error_code", length=40)
    )
    sync_error: Mapped[str | None] = mapped_column(Text)
    sync_warnings: Mapped[list[str] | None] = mapped_column(JsonDocument)
    last_sync_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- Phase 4: self-reported evidence (migration 0005) ---------------------------
    # Kept apart from ``collected_data`` (evidence collected from the platform) so
    # candidate-supplied, unverified information can never be mistaken for it.
    self_reported_data: Mapped[dict[str, Any] | None] = mapped_column(JsonDocument)
    self_reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    candidate: Mapped[Candidate] = relationship(back_populates="developer_profiles")

    def __repr__(self) -> str:
        return f"<DeveloperProfile id={self.id} platform={self.platform}>"
