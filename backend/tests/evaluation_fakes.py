"""Fixtures for Phase 5 evaluation tests (synthetic data; tests only).

The resume deliberately contains a name, e-mail, phone, links and a location so tests
can prove none of it reaches the LLM.
"""

from __future__ import annotations

import json
from typing import Any

from app.integrations.llm import ChatMessage
from app.schemas.codeforces import CodeforcesSnapshot
from app.schemas.github import GitHubSnapshot
from app.schemas.job_description_parsing import ParsedJobDescription
from app.schemas.leetcode import LeetCodeEvidence, LeetCodeEvidenceInput
from app.schemas.resume_parsing import ParsedResume
from app.services.codeforces.collector import CodeforcesCollector
from app.services.codeforces.collector import CollectorLimits as CodeforcesLimits
from app.services.evaluation.assessment import BEGIN, END
from app.services.github.collector import CollectorLimits as GitHubLimits
from app.services.github.collector import GitHubCollector
from app.services.leetcode.evidence import build_evidence
from tests import codeforces_fakes as cf
from tests import github_fakes as gf

NAME = "Jordan Sample"
EMAIL = "jordan.private@example.org"
PHONE = "+91 98765 43210"
LINKEDIN = "https://linkedin.com/in/jordan-sample-xyz"
LOCATION = "Springfield, Exampleland"
INSTITUTION = "Example Institute of Technology"


def resume_dict(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "personal_details": {
            "full_name": NAME,
            "email": EMAIL,
            "phone": PHONE,
            "location": LOCATION,
            "linkedin_url": LINKEDIN,
            "github_url": f"https://github.com/{gf.LOGIN}",
            "summary": f"{NAME} is a backend engineer. Reach me at {EMAIL} or {PHONE}.",
        },
        "education": [
            {
                "institution": INSTITUTION,
                "degree": "B.Tech",
                "field_of_study": "Computer Science",
                "end_date": "2019",
                "grade": "9.1 CGPA",
            }
        ],
        "work_experience": [
            {
                "company": "Acme Corp",
                "title": "Backend Engineer",
                "start_date": "Jan 2021",
                "end_date": "Present",
                "responsibilities": ["Built REST APIs with FastAPI and Postgres"],
                "technologies": ["Python", "FastAPI", "PostgreSQL", "Docker"],
            },
            {
                "company": "Beta Ltd",
                "title": "Software Engineer",
                "start_date": "Jul 2019",
                "end_date": "Dec 2020",
                "technologies": ["Java", "Spring Boot"],
            },
        ],
        "technical_skills": [
            {"name": "Python", "category": "language"},
            {"name": "Go", "category": "language"},
            {"name": "Kubernetes", "category": "devops"},
        ],
        "projects": [
            {"name": "Data API", "technologies": ["FastAPI", "Redis"], "description": "Public API"}
        ],
        "certifications": [{"name": "AWS Certified Developer", "issuer": "Amazon"}],
    }
    data.update(overrides)
    return data


def parsed_resume(**overrides: Any) -> ParsedResume:
    return ParsedResume.model_validate(resume_dict(**overrides))


def jd_dict(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "job_title": "Senior Backend Engineer",
        "job_summary": "Build APIs.",
        "required_skills": [
            {"name": "Python", "category": "language"},
            {"name": "PostgreSQL", "category": "database"},
            {"name": "Rust", "category": "language"},
        ],
        "preferred_skills": [
            {"name": "Kubernetes", "category": "devops"},
            {"name": "Postgres", "category": "database"},  # alias duplicate: dropped
        ],
        "minimum_experience": {"years_min": 3, "description": "backend development"},
        "education_requirements": [
            {"level": "bachelor", "field_of_study": "Computer Science", "mandatory": True}
        ],
        "required_qualifications": [{"text": "Experience designing REST APIs"}],
        "responsibilities": [{"text": "Design and build services"}],
        "soft_skills": [{"text": "Communication"}],
    }
    data.update(overrides)
    return data


def parsed_jd(**overrides: Any) -> ParsedJobDescription:
    return ParsedJobDescription.model_validate(jd_dict(**overrides))


async def github_snapshot() -> GitHubSnapshot:
    return await GitHubCollector(gf.FakeGitHub().client(), GitHubLimits()).collect(gf.LOGIN)


async def codeforces_snapshot() -> CodeforcesSnapshot:
    return await CodeforcesCollector(cf.FakeCodeforces().client(), CodeforcesLimits()).collect(
        cf.HANDLE
    )


def leetcode_evidence() -> LeetCodeEvidence:
    payload = LeetCodeEvidenceInput(
        reported_by="candidate",
        total_solved=200,
        easy_solved=100,
        medium_solved=80,
        hard_solved=20,
        languages=[{"name": "Rust", "problems_solved": 40}],  # type: ignore[list-item]
    )
    return build_evidence(payload, claimed_profile_url="https://leetcode.com/u/lc_user/")


def request_payload(messages: list[ChatMessage]) -> dict[str, Any]:
    """Decode the evaluation data the assessor sent in its first user message."""
    user = next(m for m in messages if m.role == "user").content
    body = user.split(BEGIN, 1)[1].rsplit(END, 1)[0]
    data: dict[str, Any] = json.loads(body)
    return data


def auto_reply(messages: list[ChatMessage]) -> str:
    """A deterministic 'model': met when the baseline found evidence, else insufficient."""
    payload = request_payload(messages)
    verdicts = []
    for req in payload["requirements"]:
        ids = req["baseline_evidence_ids"][:3]
        verdicts.append(
            {
                "requirement_id": req["id"],
                "verdict": "met" if ids else "insufficient_evidence",
                "evidence_ids": ids,
                "rationale": "Based on the cited evidence." if ids else "No related evidence.",
            }
        )
    first = payload["evidence"][0]["id"] if payload["evidence"] else None
    return json.dumps(
        {
            "requirements": verdicts,
            "strengths": [
                {"text": "Relevant backend work", "evidence_ids": [first] if first else []}
            ],
            "gaps": [{"text": "No Rust evidence", "requirement_ids": []}],
            "discrepancies": [],
            "summary": "The candidate shows backend experience; some skills lack evidence.",
        }
    )
