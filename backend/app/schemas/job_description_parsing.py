"""Structured job-description schema - the contract the LLM output must satisfy.

Rules (mirroring the resume schema):

* Everything is optional; a JD that does not state something yields ``null`` /
  ``[]`` - requirements are never invented.
* ``extra="forbid"`` rejects drifting output so it is retried, not stored.
* Mandatory and optional skills are separate lists.
* Every requirement may carry ``evidence``: a verbatim quote from the JD. The
  LLM is asked to always provide it; grounding drops LLM items whose evidence
  is not found in the text. Human-reviewed corrections may omit evidence.
* Technology views (languages, frameworks, databases, cloud/devops) are *derived*
  from skill categories rather than extracted twice, so they can never disagree.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]
Evidence = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]

SkillCategory = Literal[
    "language", "framework", "library", "database", "cloud", "devops", "tool", "other"
]
SeniorityLevel = Literal["intern", "junior", "mid", "senior", "lead", "principal", "manager"]
EducationLevel = Literal[
    "high_school", "diploma", "associate", "bachelor", "master", "doctorate", "other"
]


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("*", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        if isinstance(value, str) and value.strip().lower() in {
            "",
            "n/a",
            "na",
            "none",
            "null",
            "not specified",
        }:
            return None
        return value


class SkillRequirement(_Section):
    name: ShortText
    category: SkillCategory | None = None
    evidence: Evidence | None = None

    @field_validator("category", mode="before")
    @classmethod
    def _lower(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class ExperienceRequirement(_Section):
    years_min: float | None = Field(default=None, ge=0, le=50)
    years_max: float | None = Field(default=None, ge=0, le=50)
    description: LongText | None = None
    evidence: Evidence | None = None

    @model_validator(mode="after")
    def _range(self) -> ExperienceRequirement:
        if (
            self.years_min is not None
            and self.years_max is not None
            and self.years_max < self.years_min
        ):
            raise ValueError("years_max must not be smaller than years_min")
        return self

    def is_empty(self) -> bool:
        return self.years_min is None and self.years_max is None and self.description is None


class EducationRequirement(_Section):
    level: EducationLevel | None = None
    field_of_study: ShortText | None = None
    mandatory: bool | None = None
    description: LongText | None = None
    evidence: Evidence | None = None


class TextRequirement(_Section):
    """A responsibility, qualification or soft skill, with its source quote."""

    text: LongText
    evidence: Evidence | None = None


class Seniority(_Section):
    level: SeniorityLevel | None = None
    evidence: Evidence | None = None

    @field_validator("level", mode="before")
    @classmethod
    def _lower(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class ParsedJobDescription(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_title: ShortText | None = None
    company_name: ShortText | None = None
    location: ShortText | None = None
    job_summary: LongText | None = None
    required_skills: list[SkillRequirement] = Field(default_factory=list, max_length=200)
    preferred_skills: list[SkillRequirement] = Field(default_factory=list, max_length=200)
    minimum_experience: ExperienceRequirement | None = None
    preferred_experience: ExperienceRequirement | None = None
    education_requirements: list[EducationRequirement] = Field(default_factory=list, max_length=20)
    responsibilities: list[TextRequirement] = Field(default_factory=list, max_length=100)
    required_qualifications: list[TextRequirement] = Field(default_factory=list, max_length=100)
    soft_skills: list[TextRequirement] = Field(default_factory=list, max_length=50)
    seniority: Seniority | None = None

    @field_validator("job_title", "company_name", "location", "job_summary", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        if isinstance(value, str) and value.strip().lower() in {
            "",
            "n/a",
            "na",
            "none",
            "null",
            "not specified",
        }:
            return None
        return value


class TechnologySummary(BaseModel):
    """Skills grouped by category, derived from required + preferred skills."""

    programming_languages: list[str]
    frameworks_and_libraries: list[str]
    databases: list[str]
    cloud_and_devops: list[str]
    tools: list[str]
    other: list[str]

    @classmethod
    def from_parsed(cls, jd: ParsedJobDescription) -> TechnologySummary:
        groups: dict[str, list[str]] = {k: [] for k in cls.model_fields}
        mapping = {
            "language": "programming_languages",
            "framework": "frameworks_and_libraries",
            "library": "frameworks_and_libraries",
            "database": "databases",
            "cloud": "cloud_and_devops",
            "devops": "cloud_and_devops",
            "tool": "tools",
        }
        seen: set[str] = set()
        for skill in [*jd.required_skills, *jd.preferred_skills]:
            key = skill.name.lower()
            if key in seen:
                continue
            seen.add(key)
            groups[mapping.get(skill.category or "", "other")].append(skill.name)
        return cls(**groups)
