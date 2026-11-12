"""Structured resume schema - the contract the LLM output must satisfy.

Design rules:

* Every field is optional or defaults to an empty list: a resume that omits a
  section is valid, and "not stated" (``null`` / ``[]``) is never replaced by a
  guess.
* ``extra="forbid"``: unknown keys make the LLM response invalid, so a malformed
  or drifting response is rejected (and retried) instead of silently stored.
* Lengths and list sizes are bounded so a hostile document cannot produce
  unbounded output.
* Dates stay free-text strings ("Jan 2021", "2019 - Present"): resumes use many
  formats, and normalising them is a later, deterministic step - not an LLM guess.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
DateText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
UrlText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("*", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        """LLMs often emit "" or "N/A" for missing values; store them as null."""
        if isinstance(value, str) and value.strip().lower() in {"", "n/a", "na", "none", "null"}:
            return None
        return value


class PersonalDetails(_Section):
    full_name: ShortText | None = None
    email: ShortText | None = None
    phone: ShortText | None = None
    location: ShortText | None = None
    linkedin_url: UrlText | None = None
    github_url: UrlText | None = None
    portfolio_url: UrlText | None = None
    other_links: list[UrlText] = Field(default_factory=list, max_length=20)
    summary: LongText | None = None


class Education(_Section):
    institution: ShortText | None = None
    degree: ShortText | None = None
    field_of_study: ShortText | None = None
    start_date: DateText | None = None
    end_date: DateText | None = None
    grade: ShortText | None = None
    location: ShortText | None = None


class WorkExperience(_Section):
    company: ShortText | None = None
    title: ShortText | None = None
    location: ShortText | None = None
    start_date: DateText | None = None
    end_date: DateText | None = None
    is_current: bool | None = None
    responsibilities: list[LongText] = Field(default_factory=list, max_length=50)
    technologies: list[ShortText] = Field(default_factory=list, max_length=100)


class Internship(WorkExperience):
    """Same shape as work experience; kept separate so later phases can weigh them."""


class TechnicalSkill(_Section):
    name: ShortText
    category: (
        Annotated[
            str,
            StringConstraints(
                strip_whitespace=True,
                to_lower=True,
                pattern=r"^(language|framework|library|database|cloud|devops|tool|other)$",
            ),
        ]
        | None
    ) = None


class Project(_Section):
    name: ShortText | None = None
    description: LongText | None = None
    role: ShortText | None = None
    technologies: list[ShortText] = Field(default_factory=list, max_length=100)
    url: UrlText | None = None
    start_date: DateText | None = None
    end_date: DateText | None = None


class Certification(_Section):
    name: ShortText | None = None
    issuer: ShortText | None = None
    issue_date: DateText | None = None
    expiry_date: DateText | None = None
    credential_id: ShortText | None = None
    url: UrlText | None = None


class Achievement(_Section):
    description: LongText
    date: DateText | None = None


class ParsedResume(BaseModel):
    """Top-level structured resume."""

    model_config = ConfigDict(extra="forbid")

    personal_details: PersonalDetails = Field(default_factory=PersonalDetails)
    education: list[Education] = Field(default_factory=list, max_length=50)
    work_experience: list[WorkExperience] = Field(default_factory=list, max_length=100)
    internships: list[Internship] = Field(default_factory=list, max_length=50)
    technical_skills: list[TechnicalSkill] = Field(default_factory=list, max_length=300)
    projects: list[Project] = Field(default_factory=list, max_length=100)
    certifications: list[Certification] = Field(default_factory=list, max_length=100)
    achievements: list[Achievement] = Field(default_factory=list, max_length=100)

    def section_counts(self) -> dict[str, int]:
        return {
            "education": len(self.education),
            "work_experience": len(self.work_experience),
            "internships": len(self.internships),
            "technical_skills": len(self.technical_skills),
            "projects": len(self.projects),
            "certifications": len(self.certifications),
            "achievements": len(self.achievements),
        }
