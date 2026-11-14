"""Turn a parsed JD into scored requirements and match them to evidence (deterministic).

Scored requirements:

* required / preferred skills;
* minimum (required) and preferred experience;
* education requirements: ``required`` only when the JD marks them mandatory;
* required qualifications.

Responsibilities and soft skills are passed to the LLM as context only: soft
skills cannot be evidenced by resumes or platforms, so they are not scored.

The baseline for each skill lists the evidence items that mention it, grouped by
trust, so the LLM cannot silently ignore platform evidence and reviewers can see
why a verdict was given.
"""

from __future__ import annotations

import re

from app.core.enums import EvidenceTrust
from app.schemas.evaluation import BaselineStatus, EvidenceItem, RequirementMatch
from app.schemas.job_description_parsing import ExperienceRequirement, ParsedJobDescription
from app.services.evaluation.skills import mentions, skill_key

MAX_REQUIREMENTS = 60


def _experience_text(exp: ExperienceRequirement, label: str) -> str:
    if exp.years_min is not None and exp.years_max is not None:
        years = f"{exp.years_min:g}-{exp.years_max:g} years"
    elif exp.years_min is not None:
        years = f"at least {exp.years_min:g} years"
    elif exp.years_max is not None:
        years = f"up to {exp.years_max:g} years"
    else:
        years = None
    detail = exp.description or ""
    return f"{label} experience: " + "; ".join(p for p in (years, detail) if p)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9+#]+", " ", text.lower()).strip()


def _covered_quotes(jd: ParsedJobDescription) -> set[str]:
    """Source quotes already represented by skill, experience or education requirements."""
    quotes: list[str | None] = [s.evidence for s in (*jd.required_skills, *jd.preferred_skills)]
    quotes += [e.evidence for e in (jd.minimum_experience, jd.preferred_experience) if e]
    quotes += [e.evidence for e in jd.education_requirements]
    return {_norm(q) for q in quotes if q and len(_norm(q)) >= 8}


_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "or",
        "of",
        "in",
        "with",
        "on",
        "for",
        "to",
        "the",
        "strong",
        "solid",
        "good",
        "proven",
        "hands",
        "experience",
        "experienced",
        "knowledge",
        "proficiency",
        "proficient",
        "working",
        "familiarity",
        "familiar",
        "using",
        "skills",
        "skill",
        "ability",
    ]
)


def _duplicates_other_requirement(
    text: str, evidence: str | None, covered: set[str], skill_keys: set[str]
) -> bool:
    """True when a qualification restates requirements already being scored.

    Either its text / quote matches a quote behind a skill, experience or education
    requirement, or every substantive word in it is a skill that is already required.
    """
    for candidate in (text, evidence):
        if candidate and _norm(candidate) in covered:
            return True
    words = [w for w in _norm(text).split() if w not in _STOP]
    if not words:
        return False
    return all(skill_key(w) in skill_keys for w in words)


def build_requirements(jd: ParsedJobDescription) -> tuple[list[RequirementMatch], list[str]]:
    reqs: list[RequirementMatch] = []
    warnings: list[str] = []
    seen_skills: set[str] = set()

    def add(**kw: object) -> None:
        reqs.append(RequirementMatch(id=f"Q{len(reqs) + 1}", **kw))  # type: ignore[arg-type]

    for kind, skills in (("required", jd.required_skills), ("preferred", jd.preferred_skills)):
        for skill in skills:
            key = skill_key(skill.name)
            if key in seen_skills:
                continue
            seen_skills.add(key)
            add(
                kind=kind,
                type="skill",
                text=skill.name,
                skill_key=key,
                baseline_status="no_evidence",
            )
    if jd.minimum_experience is not None:
        add(
            kind="required",
            type="experience",
            text=_experience_text(jd.minimum_experience, "Minimum"),
            baseline_status="not_applicable",
        )
    if jd.preferred_experience is not None:
        add(
            kind="preferred",
            type="experience",
            text=_experience_text(jd.preferred_experience, "Preferred"),
            baseline_status="not_applicable",
        )
    for edu in jd.education_requirements:
        text = (
            " ".join(
                p
                for p in (
                    edu.level and edu.level.replace("_", " "),
                    f"in {edu.field_of_study}" if edu.field_of_study else None,
                    f"({edu.description})" if edu.description else None,
                )
                if p
            )
            or "Education requirement"
        )
        add(
            kind="required" if edu.mandatory else "preferred",
            type="education",
            text=f"Education: {text}",
            baseline_status="not_applicable",
        )
    duplicates = 0
    covered_quotes = _covered_quotes(jd)
    for qual in jd.required_qualifications:
        if _duplicates_other_requirement(qual.text, qual.evidence, covered_quotes, seen_skills):
            duplicates += 1
            continue
        add(kind="required", type="qualification", text=qual.text, baseline_status="not_applicable")
    if duplicates:
        warnings.append(
            f"{duplicates} qualification(s) repeated a skill, experience or education requirement "
            "and were not counted twice"
        )

    if len(reqs) > MAX_REQUIREMENTS:
        warnings.append(
            f"The job description has {len(reqs)} requirements; only the first "
            f"{MAX_REQUIREMENTS} (required skills first) were evaluated"
        )
        reqs = reqs[:MAX_REQUIREMENTS]
    return reqs, warnings


def _status_for(items: list[EvidenceItem]) -> BaselineStatus:
    trusts = {i.trust for i in items}
    if EvidenceTrust.PLATFORM_COLLECTED in trusts:
        return "platform_evidence"
    if EvidenceTrust.RESUME_CLAIM in trusts:
        return "resume_claim_only"
    if EvidenceTrust.SELF_REPORTED in trusts:
        return "self_reported_only"
    return "no_evidence"


def match(
    requirements: list[RequirementMatch], evidence: list[EvidenceItem]
) -> list[RequirementMatch]:
    """Fill each skill requirement's baseline; experience gets the work-history items."""
    out: list[RequirementMatch] = []
    for req in requirements:
        if req.type == "skill" and req.skill_key:
            key = req.skill_key
            hits = [i for i in evidence if key in i.skills or mentions(i.text, key)]
            out.append(
                req.model_copy(
                    update={
                        "baseline_status": _status_for(hits),
                        "baseline_evidence_ids": [i.id for i in hits][:20],
                    }
                )
            )
        elif req.type == "experience":
            hits = [i for i in evidence if i.kind == "work_experience"]
            out.append(req.model_copy(update={"baseline_evidence_ids": [i.id for i in hits][:20]}))
        elif req.type == "education":
            hits = [i for i in evidence if i.kind in ("education", "certification")]
            out.append(req.model_copy(update={"baseline_evidence_ids": [i.id for i in hits][:20]}))
        else:
            out.append(req)
    return out
