"""Domain API schemas: valid, invalid, missing and edge-case input."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.enums import CollectionStatus, EvaluationStatus, OwnershipStatus, Platform
from app.models import Candidate, DeveloperProfile, Evaluation
from app.schemas.domain import (
    CandidateCreate,
    CandidateRead,
    DeveloperProfileCreate,
    DeveloperProfileRead,
    EvaluationRead,
    JobDescriptionCreate,
)

NOW = datetime(2026, 11, 12, tzinfo=UTC)


class TestCandidateCreate:
    def test_valid_minimal(self) -> None:
        c = CandidateCreate(full_name="  Ada Lovelace  ")
        assert c.full_name == "Ada Lovelace"
        assert c.email is None
        assert c.phone is None

    def test_email_is_normalised(self) -> None:
        assert CandidateCreate(full_name="A", email="  Ada@Example.COM ").email == "ada@example.com"

    def test_blank_optional_fields_become_none(self) -> None:
        c = CandidateCreate(full_name="A", email="   ", phone="  ", location="")
        assert (c.email, c.phone, c.location) == (None, None, None)

    @pytest.mark.parametrize("name", ["", "   ", "x" * 201])
    def test_rejects_bad_full_name(self, name: str) -> None:
        with pytest.raises(ValidationError):
            CandidateCreate(full_name=name)

    def test_missing_full_name(self) -> None:
        with pytest.raises(ValidationError, match="full_name"):
            CandidateCreate()  # type: ignore[call-arg]

    @pytest.mark.parametrize(
        "email", ["no-at-sign", "a@b", "two@@example.com", "sp ace@example.com", "a@b.c"]
    )
    def test_rejects_bad_email(self, email: str) -> None:
        with pytest.raises(ValidationError, match="e-mail"):
            CandidateCreate(full_name="A", email=email)

    def test_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            CandidateCreate(full_name="A", is_admin=True)  # type: ignore[call-arg]


class TestJobDescriptionCreate:
    def test_valid(self) -> None:
        jd = JobDescriptionCreate(
            title=" Backend Engineer ",
            description_text="Python, FastAPI",
            source_url="https://jobs.example.com/123",  # type: ignore[arg-type]
        )
        assert jd.title == "Backend Engineer"
        assert jd.source_url == "https://jobs.example.com/123"
        assert jd.company is None

    @pytest.mark.parametrize("field", ["title", "description_text"])
    def test_blank_required_text_rejected(self, field: str) -> None:
        data: dict[str, object] = {"title": "T", "description_text": "D", field: "   "}
        with pytest.raises(ValidationError):
            JobDescriptionCreate.model_validate(data)

    @pytest.mark.parametrize("url", ["ftp://x.example.com", "javascript:alert(1)", "not a url"])
    def test_rejects_non_http_url(self, url: str) -> None:
        with pytest.raises(ValidationError):
            JobDescriptionCreate(title="T", description_text="D", source_url=url)  # type: ignore[arg-type]

    def test_rejects_oversized_description(self) -> None:
        with pytest.raises(ValidationError):
            JobDescriptionCreate(title="T", description_text="x" * 100_001)


class TestDeveloperProfileCreate:
    @pytest.mark.parametrize("username", ["octocat", "a", "tourist_1", "john.doe-99"])
    def test_valid_usernames(self, username: str) -> None:
        p = DeveloperProfileCreate(platform=Platform.GITHUB, username=username)
        assert p.username == username

    def test_platform_from_string(self) -> None:
        p = DeveloperProfileCreate(platform="codeforces", username="tourist")  # type: ignore[arg-type]
        assert p.platform is Platform.CODEFORCES

    @pytest.mark.parametrize(
        "username", ["", " ", "has space", "../etc/passwd", "a/b", "user?x=1", "-lead", "x" * 101]
    )
    def test_rejects_unsafe_or_malformed_usernames(self, username: str) -> None:
        with pytest.raises(ValidationError):
            DeveloperProfileCreate(platform=Platform.GITHUB, username=username)

    def test_rejects_unknown_platform(self) -> None:
        with pytest.raises(ValidationError):
            DeveloperProfileCreate(platform="myspace", username="x")  # type: ignore[arg-type]


class TestReadSchemasFromOrm:
    def test_candidate_read(self) -> None:
        cid = uuid.uuid4()
        orm = Candidate(id=cid, full_name="Ada", email=None, created_at=NOW, updated_at=NOW)
        read = CandidateRead.model_validate(orm)
        assert read.id == cid
        assert read.email is None

    def test_developer_profile_read_keeps_unavailable_distinct(self) -> None:
        orm = DeveloperProfile(
            id=uuid.uuid4(),
            candidate_id=uuid.uuid4(),
            platform=Platform.LEETCODE,
            username="ada",
            ownership_status=OwnershipStatus.UNVERIFIED,
            collection_status=CollectionStatus.UNAVAILABLE,
        )
        read = DeveloperProfileRead.model_validate(orm)
        assert read.collection_status is CollectionStatus.UNAVAILABLE
        assert read.model_dump(mode="json")["collection_status"] == "unavailable"

    def test_evaluation_read_unscored_is_null_not_zero(self) -> None:
        orm = Evaluation(
            id=uuid.uuid4(),
            candidate_id=uuid.uuid4(),
            job_description_id=uuid.uuid4(),
            status=EvaluationStatus.PENDING,
            created_at=NOW,
        )
        dumped = EvaluationRead.model_validate(orm).model_dump(mode="json")
        assert dumped["overall_score"] is None
        assert dumped["status"] == "pending"

    @pytest.mark.parametrize("score", [Decimal("-0.01"), Decimal("100.01")])
    def test_evaluation_score_out_of_range_rejected(self, score: Decimal) -> None:
        with pytest.raises(ValidationError):
            EvaluationRead(
                id=uuid.uuid4(),
                candidate_id=uuid.uuid4(),
                job_description_id=uuid.uuid4(),
                resume_id=None,
                status=EvaluationStatus.COMPLETED,
                overall_score=score,
                result=None,
                model_name=None,
                completed_at=None,
                created_at=NOW,
            )
