"""ORM models without a database: table registration, keys, relationships, DDL."""

from __future__ import annotations

import uuid
from typing import Any, cast

import pytest
from sqlalchemy import ForeignKey, Table, inspect
from sqlalchemy.dialects.postgresql.base import PGDialect
from sqlalchemy.orm import configure_mappers
from sqlalchemy.schema import CreateTable
from sqlalchemy.sql.schema import CallableColumnDefault

from app.core.enums import (
    CollectionStatus,
    EvaluationStatus,
    OwnershipStatus,
    ParseStatus,
    Platform,
)
from app.db.base import Base
from app.models import Candidate, DeveloperProfile, Evaluation, JobDescription, Resume

EXPECTED_TABLES = {"candidates", "resumes", "job_descriptions", "developer_profiles", "evaluations"}


def _table(name: str) -> Table:
    return Base.metadata.tables[name]


def _ddl(name: str) -> str:
    return str(CreateTable(_table(name)).compile(dialect=PGDialect()))  # type: ignore[no-untyped-call]


def _fk(table: str, column: str) -> ForeignKey:
    (fk,) = _table(table).c[column].foreign_keys
    return fk


def test_mappers_configure_without_errors() -> None:
    configure_mappers()


def test_exactly_the_domain_tables_are_registered() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


@pytest.mark.parametrize("table", sorted(EXPECTED_TABLES))
def test_uuid_primary_key_with_server_fallback(table: str) -> None:
    pk = list(_table(table).primary_key.columns)
    assert [c.name for c in pk] == ["id"]
    assert pk[0].type.python_type is uuid.UUID
    assert "gen_random_uuid()" in _ddl(table)
    assert f"CONSTRAINT pk_{table} PRIMARY KEY (id)" in _ddl(table)


@pytest.mark.parametrize("table", sorted(EXPECTED_TABLES))
def test_timestamps_present(table: str) -> None:
    ddl = _ddl(table)
    assert "created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL" in ddl
    assert "updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL" in ddl


def test_python_side_uuid_default_generates_unique_ids() -> None:
    default = _table("candidates").c.id.default
    assert isinstance(default, CallableColumnDefault)
    first = default.arg(cast(Any, None))  # uuid4 wrapper ignores the context
    second = default.arg(cast(Any, None))
    assert isinstance(first, uuid.UUID)
    assert first != second


@pytest.mark.parametrize(
    ("table", "column", "target", "ondelete"),
    [
        ("resumes", "candidate_id", "candidates.id", "CASCADE"),
        ("developer_profiles", "candidate_id", "candidates.id", "CASCADE"),
        ("evaluations", "candidate_id", "candidates.id", "CASCADE"),
        ("evaluations", "job_description_id", "job_descriptions.id", "RESTRICT"),
        ("evaluations", "resume_id", "resumes.id", "SET NULL"),
    ],
)
def test_foreign_keys_and_delete_rules(table: str, column: str, target: str, ondelete: str) -> None:
    fk = _fk(table, column)
    assert fk.target_fullname == target
    assert fk.ondelete == ondelete
    assert _table(table).c[column].index is True


@pytest.mark.parametrize(
    ("table", "column", "nullable"),
    [
        ("resumes", "candidate_id", False),
        ("developer_profiles", "candidate_id", False),
        ("evaluations", "candidate_id", False),
        ("evaluations", "job_description_id", False),
        ("evaluations", "resume_id", True),
        ("candidates", "email", True),
        ("candidates", "full_name", False),
        ("evaluations", "overall_score", True),
    ],
)
def test_nullability(table: str, column: str, nullable: bool) -> None:
    assert _table(table).c[column].nullable is nullable


@pytest.mark.parametrize(
    ("table", "constraint"),
    [
        ("candidates", "CONSTRAINT uq_candidates_email UNIQUE (email)"),
        ("candidates", "CONSTRAINT ck_candidates_full_name_not_blank"),
        ("resumes", "CONSTRAINT uq_resumes_candidate_id_sha256 UNIQUE (candidate_id, sha256)"),
        ("resumes", "CONSTRAINT ck_resumes_file_size_positive CHECK (file_size_bytes > 0)"),
        ("resumes", "CONSTRAINT ck_resumes_sha256_hex"),
        ("resumes", "CONSTRAINT ck_resumes_parse_status CHECK (parse_status IN ("),
        ("job_descriptions", "CONSTRAINT ck_job_descriptions_title_not_blank"),
        ("job_descriptions", "CONSTRAINT ck_job_descriptions_description_not_blank"),
        (
            "developer_profiles",
            "CONSTRAINT uq_developer_profiles_candidate_id_platform "
            "UNIQUE (candidate_id, platform)",
        ),
        ("developer_profiles", "CONSTRAINT ck_developer_profiles_platform CHECK"),
        ("developer_profiles", "CONSTRAINT ck_developer_profiles_ownership_status CHECK"),
        ("developer_profiles", "CONSTRAINT ck_developer_profiles_collection_status CHECK"),
        ("evaluations", "CONSTRAINT ck_evaluations_overall_score_range"),
        ("evaluations", "CONSTRAINT ck_evaluations_evaluation_status CHECK"),
    ],
)
def test_named_constraints(table: str, constraint: str) -> None:
    assert constraint in _ddl(table)


@pytest.mark.parametrize(
    ("table", "column", "enum_cls"),
    [
        ("resumes", "parse_status", ParseStatus),
        ("developer_profiles", "platform", Platform),
        ("developer_profiles", "ownership_status", OwnershipStatus),
        ("developer_profiles", "collection_status", CollectionStatus),
        ("evaluations", "status", EvaluationStatus),
    ],
)
def test_enums_are_stored_as_lowercase_values_not_member_names(
    table: str, column: str, enum_cls: type
) -> None:
    ddl = _ddl(table)
    for member in enum_cls:  # type: ignore[attr-defined]
        assert f"'{member.value}'" in ddl
        assert f"'{member.name}'" not in ddl or member.name == member.value
    assert f"{column} VARCHAR(20)" in ddl


@pytest.mark.parametrize(
    ("table", "column", "default"),
    [
        ("resumes", "parse_status", "pending"),
        ("developer_profiles", "ownership_status", "unverified"),
        ("developer_profiles", "collection_status", "not_collected"),
        ("evaluations", "status", "pending"),
    ],
)
def test_status_server_defaults(table: str, column: str, default: str) -> None:
    assert f"{column} VARCHAR(20) DEFAULT '{default}' NOT NULL" in _ddl(table)


def test_json_columns_are_jsonb_on_postgres() -> None:
    assert "parsed_data JSONB" in _ddl("resumes")
    assert "collected_data JSONB" in _ddl("developer_profiles")
    assert "result JSONB" in _ddl("evaluations")
    assert "analysis JSONB" in _ddl("job_descriptions")


class TestRelationships:
    def test_candidate_children_cascade_and_back_populate(self) -> None:
        rels = inspect(Candidate).relationships
        for name, target in (
            ("resumes", Resume),
            ("developer_profiles", DeveloperProfile),
            ("evaluations", Evaluation),
        ):
            rel = rels[name]
            assert rel.mapper.class_ is target
            assert rel.cascade.delete_orphan
            assert rel.passive_deletes is True
            assert rel.back_populates == "candidate"

    def test_job_description_does_not_cascade_to_evaluations(self) -> None:
        rel = inspect(JobDescription).relationships["evaluations"]
        assert not rel.cascade.delete
        assert rel.passive_deletes == "all"

    def test_back_population_in_memory(self) -> None:
        candidate = Candidate(full_name="Ada Lovelace")
        jd = JobDescription(title="Engineer", description_text="Build things")
        resume = Resume(
            original_filename="cv.pdf",
            content_type="application/pdf",
            file_size_bytes=1024,
            sha256="a" * 64,
        )
        profile = DeveloperProfile(platform=Platform.GITHUB, username="ada")
        evaluation = Evaluation(job_description=jd, resume=resume)

        candidate.resumes.append(resume)
        candidate.developer_profiles.append(profile)
        candidate.evaluations.append(evaluation)

        assert resume.candidate is candidate
        assert profile.candidate is candidate
        assert evaluation.candidate is candidate
        assert evaluation in jd.evaluations


def test_repr_never_contains_personal_data() -> None:
    candidate = Candidate(full_name="Ada Lovelace", email="ada@example.com", phone="+44 1")
    text = repr(candidate)
    assert "Ada" not in text
    assert "example.com" not in text
    assert "+44" not in text
