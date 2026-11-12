"""ORM models against a live, migrated PostgreSQL schema.

Verifies defaults, relationships, delete rules and database constraints with real
inserts. Each test runs in an isolated temporary schema (see conftest).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.enums import (
    CollectionStatus,
    EvaluationStatus,
    OwnershipStatus,
    ParseStatus,
    Platform,
)
from app.models import Candidate, DeveloperProfile, Evaluation, JobDescription, Resume

pytestmark = pytest.mark.integration

SHA = "ab" * 32


def _sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed(session: AsyncSession) -> tuple[Candidate, JobDescription, Resume, Evaluation]:
    candidate = Candidate(full_name="Test Candidate", email="test.candidate@example.com")
    jd = JobDescription(title="Backend Engineer", description_text="Python and PostgreSQL")
    resume = Resume(
        original_filename="cv.pdf",
        content_type="application/pdf",
        file_size_bytes=2048,
        sha256=SHA,
    )
    profile = DeveloperProfile(platform=Platform.GITHUB, username="test-candidate")
    evaluation = Evaluation(job_description=jd, resume=resume)
    candidate.resumes.append(resume)
    candidate.developer_profiles.append(profile)
    candidate.evaluations.append(evaluation)
    session.add_all([candidate, jd])
    await session.commit()
    return candidate, jd, resume, evaluation


async def _count(session: AsyncSession, model: type[object]) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def test_insert_with_relationships_and_defaults(migrated_engine: AsyncEngine) -> None:
    async with _sessions(migrated_engine)() as session:
        candidate, jd, resume, evaluation = await _seed(session)
        await session.refresh(resume)
        await session.refresh(evaluation)
        profile = (await session.execute(select(DeveloperProfile))).scalar_one()

    assert isinstance(candidate.id, uuid.UUID)
    assert resume.candidate_id == candidate.id
    assert resume.parse_status is ParseStatus.PENDING
    assert profile.ownership_status is OwnershipStatus.UNVERIFIED
    assert profile.collection_status is CollectionStatus.NOT_COLLECTED
    assert evaluation.status is EvaluationStatus.PENDING
    assert evaluation.overall_score is None  # unscored is NULL, not 0
    assert evaluation.job_description_id == jd.id
    assert evaluation.resume_id == resume.id
    assert candidate.created_at is not None
    assert candidate.created_at.tzinfo is not None


async def test_database_generates_uuid_for_raw_inserts(migrated_engine: AsyncEngine) -> None:
    async with migrated_engine.begin() as conn:
        new_id = (
            await conn.execute(
                text("INSERT INTO candidates (full_name) VALUES ('Raw Insert') RETURNING id")
            )
        ).scalar_one()
    assert isinstance(new_id, uuid.UUID)


async def test_enum_values_are_stored_lowercase(migrated_engine: AsyncEngine) -> None:
    async with _sessions(migrated_engine)() as session:
        await _seed(session)
    async with migrated_engine.connect() as conn:
        platform = (
            await conn.execute(text("SELECT platform FROM developer_profiles"))
        ).scalar_one()
        status = (await conn.execute(text("SELECT parse_status FROM resumes"))).scalar_one()
    assert platform == "github"
    assert status == "pending"


async def test_deleting_candidate_cascades_to_personal_data(migrated_engine: AsyncEngine) -> None:
    sessions = _sessions(migrated_engine)
    async with sessions() as session:
        candidate, _jd, _resume, _evaluation = await _seed(session)
    async with sessions() as session:
        await session.delete(await session.get(Candidate, candidate.id))
        await session.commit()
        for model in (Candidate, Resume, DeveloperProfile, Evaluation):
            assert await _count(session, model) == 0, model.__name__
        assert await _count(session, JobDescription) == 1  # JD is not personal data


async def test_job_description_with_evaluations_cannot_be_deleted(
    migrated_engine: AsyncEngine,
) -> None:
    async with _sessions(migrated_engine)() as session:
        _candidate, jd, _resume, _evaluation = await _seed(session)
    async with migrated_engine.connect() as conn:
        with pytest.raises(IntegrityError, match="fk_evaluations_job_description_id"):
            await conn.execute(text("DELETE FROM job_descriptions WHERE id = :id"), {"id": jd.id})


async def test_deleting_resume_keeps_evaluation(migrated_engine: AsyncEngine) -> None:
    sessions = _sessions(migrated_engine)
    async with sessions() as session:
        _candidate, _jd, resume, evaluation = await _seed(session)
    async with migrated_engine.begin() as conn:
        await conn.execute(text("DELETE FROM resumes WHERE id = :id"), {"id": resume.id})
    async with sessions() as session:
        kept = await session.get(Evaluation, evaluation.id)
        assert kept is not None
        assert kept.resume_id is None


@pytest.mark.parametrize(
    ("sql", "constraint"),
    [
        ("INSERT INTO candidates (full_name) VALUES ('   ')", "ck_candidates_full_name_not_blank"),
        (
            "INSERT INTO job_descriptions (title, description_text) VALUES ('', 'x')",
            "ck_job_descriptions_title_not_blank",
        ),
        (
            "INSERT INTO developer_profiles (candidate_id, platform, username) "
            "VALUES (:cid, 'myspace', 'x')",
            "ck_developer_profiles_platform",
        ),
        (
            "INSERT INTO developer_profiles (candidate_id, platform, username, ownership_status) "
            "VALUES (:cid, 'kaggle', 'x', 'trusted')",
            "ck_developer_profiles_ownership_status",
        ),
        (
            "INSERT INTO resumes (candidate_id, original_filename, content_type, "
            "file_size_bytes, sha256) VALUES (:cid, 'a.pdf', 'application/pdf', 0, :sha)",
            "ck_resumes_file_size_positive",
        ),
        (
            "INSERT INTO resumes (candidate_id, original_filename, content_type, "
            "file_size_bytes, sha256) VALUES (:cid, 'a.pdf', 'application/pdf', 1, 'NOT-HEX')",
            "ck_resumes_sha256_hex",
        ),
        (
            "INSERT INTO evaluations (candidate_id, job_description_id, overall_score) "
            "VALUES (:cid, :jid, 100.5)",
            "ck_evaluations_overall_score_range",
        ),
        (
            "INSERT INTO evaluations (candidate_id, job_description_id, status) "
            "VALUES (:cid, :jid, 'done')",
            "ck_evaluations_evaluation_status",
        ),
        (
            "INSERT INTO resumes (candidate_id, original_filename, content_type, "
            "file_size_bytes, sha256) VALUES (gen_random_uuid(), 'a.pdf', 'x', 1, :sha)",
            "fk_resumes_candidate_id_candidates",
        ),
    ],
)
async def test_database_constraints_reject_invalid_rows(
    migrated_engine: AsyncEngine, sql: str, constraint: str
) -> None:
    async with _sessions(migrated_engine)() as session:
        candidate, jd, _resume, _evaluation = await _seed(session)
    params = {"cid": candidate.id, "jid": jd.id, "sha": "cd" * 32}
    async with migrated_engine.connect() as conn:
        with pytest.raises(IntegrityError, match=constraint):
            await conn.execute(text(sql), {k: v for k, v in params.items() if f":{k}" in sql})


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        (
            "INSERT INTO candidates (full_name, email) "
            "VALUES ('Dup', 'test.candidate@example.com')",
            "uq_candidates_email",
        ),
        (
            "INSERT INTO developer_profiles (candidate_id, platform, username) "
            "VALUES (:cid, 'github', 'another-account')",
            "uq_developer_profiles_candidate_id_platform",
        ),
        (
            "INSERT INTO resumes (candidate_id, original_filename, content_type, "
            "file_size_bytes, sha256) VALUES (:cid, 'same.pdf', 'application/pdf', 1, :sha)",
            "uq_resumes_candidate_id_sha256",
        ),
    ],
)
async def test_unique_constraints(
    migrated_engine: AsyncEngine, statement: str, constraint: str
) -> None:
    async with _sessions(migrated_engine)() as session:
        candidate, _jd, _resume, _evaluation = await _seed(session)
    params = {"cid": candidate.id, "sha": SHA}
    async with migrated_engine.connect() as conn:
        with pytest.raises(IntegrityError, match=constraint):
            await conn.execute(
                text(statement), {k: v for k, v in params.items() if f":{k}" in statement}
            )


async def test_orm_rejects_invalid_enum_value_before_sending(migrated_engine: AsyncEngine) -> None:
    async with _sessions(migrated_engine)() as session:
        candidate, *_ = await _seed(session)
        session.add(DeveloperProfile(candidate_id=candidate.id, platform="myspace", username="x"))
        with pytest.raises(StatementError):
            await session.flush()


async def test_numeric_score_round_trip(migrated_engine: AsyncEngine) -> None:
    sessions = _sessions(migrated_engine)
    async with sessions() as session:
        _candidate, _jd, _resume, evaluation = await _seed(session)
        evaluation.overall_score = Decimal("87.25")
        evaluation.status = EvaluationStatus.COMPLETED
        await session.commit()
    async with sessions() as session:
        stored = await session.get(Evaluation, evaluation.id)
        assert stored is not None
        assert stored.overall_score == Decimal("87.25")
        assert stored.status is EvaluationStatus.COMPLETED
