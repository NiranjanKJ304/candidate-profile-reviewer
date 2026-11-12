"""Synthetic job descriptions for tests (fictional company, no real posting)."""

from __future__ import annotations

import json
from typing import Any

from tests import resume_fixtures as rf

JD_LINES = [
    "Senior Backend Engineer",
    "Company: Example Analytics Ltd",
    "Location: Springfield (Hybrid)",
    "",
    "About the role",
    "We are looking for a Senior Backend Engineer to build data APIs.",
    "",
    "Responsibilities",
    "- Design and build REST APIs in Python",
    "- Own PostgreSQL schema design and performance",
    "",
    "Requirements",
    "- 5+ years of professional software development experience",
    "- Strong experience with Python and FastAPI",
    "- Experience with PostgreSQL",
    "- Bachelor's degree in Computer Science or related field",
    "",
    "Nice to have",
    "- Experience with Docker and Kubernetes",
    "- AWS experience is a plus",
    "",
    "Soft skills",
    "- Excellent communication skills",
]
JD_TEXT = "\n".join(JD_LINES)


def jd_txt(text: str = JD_TEXT, encoding: str = "utf-8") -> bytes:
    return text.encode(encoding)


def jd_pdf() -> bytes:
    return rf.text_pdf(JD_LINES)


def jd_scanned_pdf() -> bytes:
    return rf.scanned_pdf(JD_LINES)


def jd_docx() -> bytes:
    return rf.make_docx(JD_LINES)


def llm_jd_json(**overrides: Any) -> str:
    """A structured response consistent with JD_TEXT (all evidence is verbatim)."""
    data: dict[str, Any] = {
        "job_title": "Senior Backend Engineer",
        "company_name": "Example Analytics Ltd",
        "location": "Springfield (Hybrid)",
        "job_summary": "Build data APIs as a senior backend engineer.",
        "required_skills": [
            {
                "name": "Python",
                "category": "language",
                "evidence": "Strong experience with Python and FastAPI",
            },
            {
                "name": "FastAPI",
                "category": "framework",
                "evidence": "Strong experience with Python and FastAPI",
            },
            {
                "name": "PostgreSQL",
                "category": "database",
                "evidence": "Experience with PostgreSQL",
            },
        ],
        "preferred_skills": [
            {
                "name": "Docker",
                "category": "devops",
                "evidence": "Experience with Docker and Kubernetes",
            },
            {
                "name": "Kubernetes",
                "category": "devops",
                "evidence": "Experience with Docker and Kubernetes",
            },
            {"name": "AWS", "category": "cloud", "evidence": "AWS experience is a plus"},
        ],
        "minimum_experience": {
            "years_min": 5,
            "years_max": None,
            "description": "Professional software development",
            "evidence": "5+ years of professional software development experience",
        },
        "preferred_experience": None,
        "education_requirements": [
            {
                "level": "bachelor",
                "field_of_study": "Computer Science",
                "mandatory": True,
                "description": None,
                "evidence": "Bachelor's degree in Computer Science or related field",
            }
        ],
        "responsibilities": [
            {
                "text": "Design and build REST APIs in Python",
                "evidence": "Design and build REST APIs in Python",
            },
            {
                "text": "Own PostgreSQL schema design and performance",
                "evidence": "Own PostgreSQL schema design and performance",
            },
        ],
        "required_qualifications": [
            {
                "text": "5+ years of professional software development experience",
                "evidence": "5+ years of professional software development experience",
            }
        ],
        "soft_skills": [{"text": "Communication", "evidence": "Excellent communication skills"}],
        "seniority": {"level": "senior", "evidence": "Senior Backend Engineer"},
    }
    data.update(overrides)
    return json.dumps(data)
