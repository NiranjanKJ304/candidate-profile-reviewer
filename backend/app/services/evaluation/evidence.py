"""Build the identity-redacted evidence bundle for one candidate (deterministic, no LLM).

Each fact becomes an ``EvidenceItem`` with an id, its source and a trust level:

* ``resume_claim`` (R*): stated in the parsed resume;
* ``platform_collected`` (G*, C*): collected from GitHub / Codeforces official APIs
  (the account's ownership is still unverified);
* ``self_reported`` (L*): LeetCode evidence entered manually, not verifiable.

Privacy and fairness: the candidate's name, e-mail, phone, links and platform
usernames are redacted from all text; contact details, location, institution names,
grades, GitHub bio/company/location and follower counts are not included at all.
README content is not included (untrusted, long, may contain instructions).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from app.core.enums import AvailabilityStatus, EvidenceTrust
from app.schemas.codeforces import CodeforcesSnapshot
from app.schemas.evaluation import EvidenceItem, EvidenceSource, ExperienceEstimate, SourceStatus
from app.schemas.github import GitHubSnapshot
from app.schemas.leetcode import LeetCodeEvidence
from app.schemas.resume_parsing import ParsedResume
from app.services.evaluation.experience import estimate_experience
from app.services.evaluation.redact import Redactor
from app.services.evaluation.skills import ALGORITHMIC_TAGS, language_key, skill_key, tag_key

MAX_RESPONSIBILITIES = 4
MAX_REPOSITORIES = 15
MAX_TEXT = 600
_COLLECTED = (AvailabilityStatus.AVAILABLE, AvailabilityStatus.EMPTY)


SourceState = Literal["used", "not_linked", "not_collected", "unavailable"]


@dataclass
class PlatformInput:
    """What the processor found for one platform."""

    status: SourceState
    note: str | None = None
    collected_at: datetime | None = None


@dataclass
class EvidenceBundle:
    items: list[EvidenceItem] = field(default_factory=list)
    sources: list[SourceStatus] = field(default_factory=list)
    experience: ExperienceEstimate = field(
        default_factory=lambda: ExperienceEstimate(
            years=None, entries_used=0, entries_unparsed=0, method="No resume"
        )
    )
    redactions: dict[str, int] = field(default_factory=dict)

    def by_id(self) -> dict[str, EvidenceItem]:
        return {item.id: item for item in self.items}


class _Builder:
    def __init__(self, redact: Redactor) -> None:
        self.redact = redact
        self.items: list[EvidenceItem] = []
        self._counters = {"resume": 0, "github": 0, "codeforces": 0, "leetcode": 0}
        self._prefix = {"resume": "R", "github": "G", "codeforces": "C", "leetcode": "L"}

    def add(
        self,
        source: EvidenceSource,
        trust: EvidenceTrust,
        kind: str,
        text: str,
        skills: list[str] | None = None,
        source_url: str | None = None,
    ) -> None:
        clean = self.redact(text).strip()
        if not clean:
            return
        self._counters[source] += 1
        keys = sorted({k for k in (skills or []) if k})
        self.items.append(
            EvidenceItem(
                id=f"{self._prefix[source]}{self._counters[source]}",
                source=source,
                trust=trust,
                kind=kind,
                text=clean[:MAX_TEXT],
                skills=keys,
                source_url=source_url,
            )
        )


def _join(parts: list[str | None], sep: str = "; ") -> str:
    return sep.join(p for p in parts if p)


def _resume_items(b: _Builder, resume: ParsedResume) -> None:
    claim = EvidenceTrust.RESUME_CLAIM
    for skill in resume.technical_skills:
        category = f" ({skill.category})" if skill.category else ""
        b.add(
            "resume",
            claim,
            "skill",
            f"Lists skill: {skill.name}{category}",
            [skill_key(skill.name)],
        )
    for kind, entries in (
        ("work_experience", resume.work_experience),
        ("internship", resume.internships),
    ):
        for job in entries:
            when = _join(
                [job.start_date, job.end_date or ("present" if job.is_current else None)], " - "
            )
            duties = "; ".join(job.responsibilities[:MAX_RESPONSIBILITIES])
            tech = ", ".join(job.technologies)
            text = _join(
                [
                    ("Internship" if kind == "internship" else "Role")
                    + f": {job.title or 'untitled role'}"
                    + (f" at {job.company}" if job.company else ""),
                    f"dates: {when}" if when else None,
                    f"technologies: {tech}" if tech else None,
                    f"responsibilities: {duties}" if duties else None,
                ]
            )
            b.add("resume", claim, kind, text, [skill_key(t) for t in job.technologies])
    for project in resume.projects:
        tech = ", ".join(project.technologies)
        text = _join(
            [
                f"Project: {project.name or 'unnamed'}",
                f"role: {project.role}" if project.role else None,
                f"technologies: {tech}" if tech else None,
                project.description,
            ]
        )
        b.add("resume", claim, "project", text, [skill_key(t) for t in project.technologies])
    for edu in resume.education:
        # Institution and grade are deliberately omitted (fairness).
        text = _join(
            [
                edu.degree,
                f"field: {edu.field_of_study}" if edu.field_of_study else None,
                f"completed: {edu.end_date}" if edu.end_date else None,
            ]
        )
        if text:
            b.add("resume", claim, "education", f"Education: {text}")
    for cert in resume.certifications:
        if cert.name:
            b.add(
                "resume",
                claim,
                "certification",
                _join(
                    [
                        f"Certification: {cert.name}",
                        f"issuer: {cert.issuer}" if cert.issuer else None,
                    ]
                ),
            )
    for ach in resume.achievements[:10]:
        b.add("resume", claim, "achievement", f"Achievement: {ach.description}")
    if resume.personal_details.summary:
        b.add("resume", claim, "summary", f"Resume summary: {resume.personal_details.summary}")


def _github_items(b: _Builder, snap: GitHubSnapshot) -> None:
    trust = EvidenceTrust.PLATFORM_COLLECTED
    for lang in snap.languages.items:
        key = language_key(lang.name)
        b.add(
            "github",
            trust,
            "language",
            f"{lang.repositories} public non-fork GitHub repositories "
            f"with primary language {lang.name}",
            [key] if key else [],
        )
    for topic in snap.topics.items[:20]:
        b.add(
            "github",
            trust,
            "topic",
            f"GitHub topic '{topic.name}' on {topic.repositories} non-fork repositories",
            [skill_key(topic.name)],
        )
    repos = [r for r in snap.repositories.items if not r.is_fork][:MAX_REPOSITORIES]
    for repo in repos:
        lang_key = language_key(repo.primary_language) if repo.primary_language else None
        text = _join(
            [
                f"GitHub repository '{repo.name}'",
                repo.description,
                f"language: {repo.primary_language}" if repo.primary_language else None,
                f"topics: {', '.join(repo.topics)}" if repo.topics else None,
                f"last push: {repo.pushed_at.date().isoformat()}" if repo.pushed_at else None,
                "archived" if repo.is_archived else None,
            ]
        )
        skills = [skill_key(t) for t in repo.topics] + ([lang_key] if lang_key else [])
        b.add("github", trust, "repository", text, skills, repo.html_url)
    act = snap.activity
    if act.status in _COLLECTED:
        b.add(
            "github",
            trust,
            "activity",
            f"Public GitHub activity (last ~90 days): {act.events_analyzed} events, "
            f"{act.push_events} pushes",
        )
    for label, section in (("pull requests", snap.pull_requests), ("issues", snap.issues)):
        if section.status is AvailabilityStatus.AVAILABLE and section.total_count is not None:
            b.add(
                "github",
                trust,
                "contributions",
                f"Authored {section.total_count} public GitHub {label}",
            )


def _codeforces_items(b: _Builder, snap: CodeforcesSnapshot) -> None:
    trust = EvidenceTrust.PLATFORM_COLLECTED
    life = snap.analytics.lifetime
    if life.current_rating is not None or life.rated_contests is not None:
        b.add(
            "codeforces",
            trust,
            "rating",
            _join(
                [
                    f"Codeforces rating {life.current_rating} "
                    f"(max {life.max_rating}, rank {life.rank})"
                    if life.current_rating is not None
                    else "Codeforces account is unrated",
                    f"{life.rated_contests} rated contests"
                    if life.rated_contests is not None
                    else None,
                ]
            ),
            ["competitive programming", "data structures and algorithms"],
        )
    stats = snap.analytics.submissions
    if stats is not None:
        window = "most recent " if stats.truncated else ""
        b.add(
            "codeforces",
            trust,
            "problem_solving",
            f"Codeforces: {stats.accepted_unique_problems} unique problems accepted out of "
            f"{stats.attempted_unique_problems} attempted in the {window}"
            f"{stats.total_submissions} submissions",
            ["competitive programming", "data structures and algorithms"],
        )
        for tag in stats.accepted_by_tag[:12]:
            skills = [tag_key(tag.name)]
            if tag.name in ALGORITHMIC_TAGS:
                skills.append("data structures and algorithms")
            b.add(
                "codeforces",
                trust,
                "problem_topic",
                f"Codeforces: {tag.count} accepted unique problems tagged '{tag.name}'",
                skills,
            )
        for lang in stats.languages[:5]:
            key = language_key(lang.name)
            b.add(
                "codeforces",
                trust,
                "language",
                f"Codeforces: {lang.count} submissions in {lang.name}",
                [key] if key else [],
            )


def _leetcode_items(b: _Builder, ev: LeetCodeEvidence) -> None:
    trust = EvidenceTrust.SELF_REPORTED
    note = " (self-reported, not verified)"
    s = ev.solved
    if s.status is AvailabilityStatus.AVAILABLE:
        parts = [
            f"{label} {value}"
            for label, value in (
                ("total", s.total),
                ("easy", s.easy),
                ("medium", s.medium),
                ("hard", s.hard),
            )
            if value is not None
        ]
        b.add(
            "leetcode",
            trust,
            "solved",
            "LeetCode solved problems: " + ", ".join(parts) + note,
            ["data structures and algorithms"],
        )
    c = ev.contest
    if c.status is AvailabilityStatus.AVAILABLE:
        parts = [
            f"{label} {value}"
            for label, value in (
                ("rating", c.rating),
                ("contests attended", c.attended),
                ("top %", c.top_percentage),
            )
            if value is not None
        ]
        b.add(
            "leetcode",
            trust,
            "contest",
            "LeetCode contests: " + ", ".join(parts) + note,
            ["competitive programming"],
        )
    for lang in ev.languages.items:
        count = f" ({lang.problems_solved} problems)" if lang.problems_solved is not None else ""
        key = language_key(lang.name)
        b.add(
            "leetcode",
            trust,
            "language",
            f"LeetCode language: {lang.name}{count}{note}",
            [key] if key else [],
        )
    for topic in ev.topics.items:
        count = f" ({topic.problems_solved} problems)" if topic.problems_solved is not None else ""
        b.add(
            "leetcode",
            trust,
            "topic",
            f"LeetCode topic: {topic.name}{count}{note}",
            [skill_key(topic.name)],
        )


def build_bundle(
    *,
    resume: ParsedResume | None,
    github: GitHubSnapshot | None,
    codeforces: CodeforcesSnapshot | None,
    leetcode: LeetCodeEvidence | None,
    platform_status: dict[str, PlatformInput],
    identifiers: list[str],
    today: date | None = None,
) -> EvidenceBundle:
    name = resume.personal_details.full_name if resume else None
    redact = Redactor(name=name, identifiers=identifiers)
    b = _Builder(redact)
    bundle = EvidenceBundle()

    if resume is not None:
        _resume_items(b, resume)
        bundle.experience = estimate_experience(resume.work_experience, today=today)
        bundle.sources.append(SourceStatus(source="resume", status="used"))
    else:
        bundle.sources.append(
            SourceStatus(
                source="resume", status="unavailable", note="No parsed resume for this candidate"
            )
        )
    if github is not None:
        _github_items(b, github)
    if codeforces is not None:
        _codeforces_items(b, codeforces)
    if leetcode is not None:
        _leetcode_items(b, leetcode)
    platforms: tuple[EvidenceSource, ...] = ("github", "codeforces", "leetcode")
    for source in platforms:
        info = platform_status.get(source, PlatformInput(status="not_linked"))
        bundle.sources.append(
            SourceStatus(
                source=source, status=info.status, note=info.note, collected_at=info.collected_at
            )
        )
    bundle.items = b.items
    bundle.redactions = dict(redact.counts)
    return bundle
