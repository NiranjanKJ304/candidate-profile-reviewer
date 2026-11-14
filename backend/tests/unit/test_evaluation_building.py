"""Phase 5 deterministic parts: redaction, skills, experience, evidence, matching, scoring."""

from __future__ import annotations

from datetime import date

import pytest

from app.core.enums import EvidenceTrust, Verdict
from app.schemas.evaluation import AssessedRequirement, EvidenceItem
from app.schemas.resume_parsing import WorkExperience
from app.services.evaluation.evidence import PlatformInput, build_bundle
from app.services.evaluation.experience import estimate_experience, parse_month
from app.services.evaluation.matching import MAX_REQUIREMENTS, build_requirements, match
from app.services.evaluation.redact import Redactor
from app.services.evaluation.scoring import score
from app.services.evaluation.skills import language_key, mentions, skill_key, tag_key
from tests import codeforces_fakes as cf
from tests import evaluation_fakes as ef
from tests import github_fakes as gf

TODAY = date(2026, 11, 14)
USED = {
    "github": PlatformInput("used"),
    "codeforces": PlatformInput("used"),
    "leetcode": PlatformInput("used", "Self-reported, not verified"),
}


# --------------------------------------------------------------------------- redaction
class TestRedactor:
    def test_removes_identity_and_counts(self) -> None:
        r = Redactor(name="Jordan A. Sample", identifiers=["octo-dev"])
        out = r(
            "Jordan Sample (jordan@x.org, +91 98765 43210) see https://x.io/a and www.y.com; "
            "repo octo-dev/api. Sample leads."
        )
        assert "Jordan" not in out
        assert "Sample" not in out
        assert "jordan@x.org" not in out
        assert "98765" not in out
        assert "x.io" not in out
        assert "octo-dev" not in out
        assert r.counts["emails"] == 1
        assert r.counts["phones"] == 1
        assert r.counts["urls"] == 2
        assert r.counts["names"] >= 3

    @pytest.mark.parametrize(
        "text", ["2019 - 2022", "Jan 2019 - Dec 2022", "Reduced latency by 45%", "v3.11.4"]
    )
    def test_keeps_dates_and_numbers(self, text: str) -> None:
        assert Redactor(name="Al")(text) == text

    def test_short_name_parts_are_not_redacted_everywhere(self) -> None:
        # "Al" (2 letters) would destroy words like "algorithm"
        assert Redactor(name="Al Li")("algorithms") == "algorithms"

    def test_empty(self) -> None:
        assert Redactor()(None) == ""


# --------------------------------------------------------------------------- skills
class TestSkills:
    @pytest.mark.parametrize(
        ("raw", "key"),
        [("Postgres", "postgresql"), ("ReactJS", "react"), ("K8s", "kubernetes"),
         ("Python 3", "python"), ("  Node JS ", "node.js"), ("Rust", "rust"),
         ("REST APIs", "rest")],
    )  # fmt: skip
    def test_skill_key(self, raw: str, key: str) -> None:
        assert skill_key(raw) == key

    @pytest.mark.parametrize(
        ("raw", "key"),
        [("GNU C++17 (64)", "c++"), ("PyPy 3-64", "python"), ("Java 21", "java"),
         ("JavaScript", "javascript"), ("Go", "go"), ("GNU C11", "c"), ("Kotlin 1.9", "kotlin"),
         ("Jupyter Notebook", "python"), ("C# 10", "c#"), ("Rust 2021", "rust")],
    )  # fmt: skip
    def test_language_key(self, raw: str, key: str) -> None:
        assert language_key(raw) == key

    def test_mentions_whole_words_and_aliases(self) -> None:
        assert mentions("Built APIs with Postgres", "postgresql")
        assert not mentions("Java developer", "javascript")
        assert not mentions("going places, google", "go")  # whole words only
        assert mentions("Golang services", "go")  # alias
        assert mentions("Used Go and Rust", "go")
        assert mentions("C++ and C#", "c++")

    def test_tag_key(self) -> None:
        assert tag_key("dp") == "dynamic programming"
        assert tag_key("graphs") == "graph algorithms"


# --------------------------------------------------------------------------- experience
class TestExperience:
    @pytest.mark.parametrize(
        ("text", "end", "expected"),
        [("Jan 2021", False, 2021 * 12), ("2019", False, 2019 * 12), ("2019", True, 2019 * 12 + 11),
         ("03/2020", False, 2020 * 12 + 2), ("Present", True, 2026 * 12 + 10),
         ("soon", False, None),
         (None, False, None)],
    )  # fmt: skip
    def test_parse_month(self, text: str | None, end: bool, expected: int | None) -> None:
        assert parse_month(text, end=end, today=TODAY) == expected

    def test_merges_overlaps_and_reports_unparsed(self) -> None:
        jobs = [
            WorkExperience(start_date="Jan 2021", end_date="Present"),
            WorkExperience(start_date="Jun 2022", end_date="Dec 2022"),  # overlap
            WorkExperience(start_date="2019 - 2020"),  # range in one field
            WorkExperience(title="no dates"),
            WorkExperience(start_date="2025", end_date="2020"),  # inverted
        ]
        est = estimate_experience(jobs, today=TODAY)
        assert est.entries_used == 3
        assert est.entries_unparsed == 2
        # 2019-01..2020-12 (24 months) + 2021-01..2026-11 (71 months)
        assert est.years == round(95 / 12, 1)

    def test_unknown_is_none_not_zero(self) -> None:
        est = estimate_experience([WorkExperience(title="x")], today=TODAY)
        assert est.years is None
        assert "unknown" in est.method


# --------------------------------------------------------------------------- evidence
class TestEvidenceBundle:
    async def test_all_sources_with_trust_levels(self) -> None:
        bundle = build_bundle(
            resume=ef.parsed_resume(),
            github=await ef.github_snapshot(),
            codeforces=await ef.codeforces_snapshot(),
            leetcode=ef.leetcode_evidence(),
            platform_status=USED,
            identifiers=[gf.LOGIN, cf.HANDLE, "lc_user"],
            today=TODAY,
        )
        trusts = {i.source: i.trust for i in bundle.items}
        assert trusts == {
            "resume": EvidenceTrust.RESUME_CLAIM,
            "github": EvidenceTrust.PLATFORM_COLLECTED,
            "codeforces": EvidenceTrust.PLATFORM_COLLECTED,
            "leetcode": EvidenceTrust.SELF_REPORTED,
        }
        ids = [i.id for i in bundle.items]
        assert len(ids) == len(set(ids))
        assert {i[0] for i in ids} == {"R", "G", "C", "L"}
        assert [s.source for s in bundle.sources] == ["resume", "github", "codeforces", "leetcode"]
        assert bundle.experience.years is not None
        assert all(i.text.endswith("(self-reported, not verified)")
                   for i in bundle.items if i.source == "leetcode")  # fmt: skip

    async def test_no_identity_or_bias_prone_data_in_texts(self) -> None:
        bundle = build_bundle(
            resume=ef.parsed_resume(),
            github=await ef.github_snapshot(),
            codeforces=await ef.codeforces_snapshot(),
            leetcode=ef.leetcode_evidence(),
            platform_status=USED,
            identifiers=[gf.LOGIN, cf.HANDLE, "lc_user"],
        )
        texts = " ".join(i.text for i in bundle.items)
        for secret in (ef.NAME, "Jordan", ef.EMAIL, "98765", "linkedin", ef.LOCATION,
                       ef.INSTITUTION, "9.1 CGPA", gf.LOGIN, cf.HANDLE, gf.PRIVATE_EMAIL,
                       "Example Corp", "followers"):  # fmt: skip
            assert secret not in texts, secret
        assert bundle.redactions["names"] >= 1
        assert bundle.redactions["emails"] >= 1
        assert bundle.redactions["phones"] >= 1

    def test_resume_only(self) -> None:
        bundle = build_bundle(
            resume=ef.parsed_resume(), github=None, codeforces=None, leetcode=None,
            platform_status={}, identifiers=[], today=TODAY,
        )  # fmt: skip
        assert {i.source for i in bundle.items} == {"resume"}
        statuses = {s.source: s.status for s in bundle.sources}
        assert statuses == {"resume": "used", "github": "not_linked", "codeforces": "not_linked",
                            "leetcode": "not_linked"}  # fmt: skip
        kinds = {i.kind for i in bundle.items}
        assert {"skill", "work_experience", "project", "education", "certification"} <= kinds

    def test_no_resume_no_platforms_is_empty(self) -> None:
        bundle = build_bundle(
            resume=None, github=None, codeforces=None, leetcode=None,
            platform_status={}, identifiers=[],
        )  # fmt: skip
        assert bundle.items == []
        assert bundle.experience.years is None
        assert bundle.sources[0].status == "unavailable"

    async def test_github_skips_forks_and_keeps_source_url(self) -> None:
        bundle = build_bundle(
            resume=None, github=await ef.github_snapshot(), codeforces=None, leetcode=None,
            platform_status=USED, identifiers=[gf.LOGIN],
        )  # fmt: skip
        repos = [i for i in bundle.items if i.kind == "repository"]
        assert repos
        assert not any("forked-lib" in i.text for i in repos)
        assert all(i.source_url and i.source_url.startswith("https://github.com/") for i in repos)


# --------------------------------------------------------------------------- matching
class TestMatching:
    def test_requirements_from_jd(self) -> None:
        reqs, warnings = build_requirements(ef.parsed_jd())
        assert warnings == []
        summary = [(r.id, r.kind, r.type, r.text) for r in reqs]
        assert summary[:4] == [
            ("Q1", "required", "skill", "Python"),
            ("Q2", "required", "skill", "PostgreSQL"),
            ("Q3", "required", "skill", "Rust"),
            ("Q4", "preferred", "skill", "Kubernetes"),  # alias duplicate Postgres dropped
        ]
        types = [r.type for r in reqs]
        assert types.count("experience") == 1
        assert types.count("education") == 1
        assert types.count("qualification") == 1
        assert not any("Communication" in r.text for r in reqs)  # soft skills not scored
        edu = next(r for r in reqs if r.type == "education")
        assert edu.kind == "required"  # mandatory

    def test_qualifications_repeating_other_requirements_are_not_counted_twice(self) -> None:
        jd = ef.parsed_jd(
            required_skills=[
                {"name": "Python", "evidence": "Strong experience with Python and FastAPI"},
                {"name": "FastAPI", "evidence": "Strong experience with Python and FastAPI"},
                {"name": "PostgreSQL"},
            ],
            preferred_skills=[],
            minimum_experience={
                "years_min": 5,
                "evidence": "5+ years of professional software development experience",
            },
            education_requirements=[{"level": "bachelor", "mandatory": True,
                                     "evidence": "Bachelor's degree in Computer Science"}],
            required_qualifications=[
                {"text": "5+ years of professional software development experience"},
                {"text": "Strong experience with Python and FastAPI"},
                {"text": "Experience with PostgreSQL"},  # only restates a required skill
                {"text": "Bachelor's degree in Computer Science"},
                {"text": "Experience leading a team of engineers"},  # genuinely new
            ],
        )  # fmt: skip
        reqs, warnings = build_requirements(jd)
        quals = [r.text for r in reqs if r.type == "qualification"]
        assert quals == ["Experience leading a team of engineers"]
        assert any("4 qualification(s) repeated" in w for w in warnings)

    def test_non_mandatory_education_is_preferred(self) -> None:
        jd = ef.parsed_jd(education_requirements=[{"level": "master", "mandatory": None}])
        reqs, _ = build_requirements(jd)
        assert next(r for r in reqs if r.type == "education").kind == "preferred"

    def test_requirement_cap(self) -> None:
        many = [{"name": f"skill{i}"} for i in range(MAX_REQUIREMENTS + 5)]
        reqs, warnings = build_requirements(ef.parsed_jd(required_skills=many))
        assert len(reqs) == MAX_REQUIREMENTS
        assert warnings

    def test_baseline_by_trust(self) -> None:
        reqs, _ = build_requirements(ef.parsed_jd())
        evidence = [
            EvidenceItem(id="R1", source="resume", trust=EvidenceTrust.RESUME_CLAIM, kind="skill",
                         text="Lists skill: Python", skills=["python"]),
            EvidenceItem(id="G1", source="github", trust=EvidenceTrust.PLATFORM_COLLECTED,
                         kind="language", text="3 repositories in Python", skills=["python"]),
            EvidenceItem(id="R2", source="resume", trust=EvidenceTrust.RESUME_CLAIM,
                         kind="work_experience", text="Role: built APIs with Postgres", skills=[]),
            EvidenceItem(id="L1", source="leetcode", trust=EvidenceTrust.SELF_REPORTED,
                         kind="language", text="LeetCode language: Rust", skills=["rust"]),
        ]  # fmt: skip
        out = {r.text: r for r in match(reqs, evidence)}
        assert out["Python"].baseline_status == "platform_evidence"
        assert out["Python"].baseline_evidence_ids == ["R1", "G1"]
        assert out["PostgreSQL"].baseline_status == "resume_claim_only"  # text mention, alias
        assert out["Rust"].baseline_status == "self_reported_only"
        assert out["Kubernetes"].baseline_status == "no_evidence"
        exp = next(r for r in out.values() if r.type == "experience")
        assert exp.baseline_evidence_ids == ["R2"]


# --------------------------------------------------------------------------- scoring
def _req(rid: str, kind: str, verdict: Verdict, credit: float) -> AssessedRequirement:
    return AssessedRequirement(
        id=rid, kind=kind, type="skill", text=rid, baseline_status="no_evidence",  # type: ignore[arg-type]
        verdict=verdict, rationale="x", weight=2.0 if kind == "required" else 1.0, credit=credit,
    )  # fmt: skip


class TestScoring:
    def test_weighted_score_and_label(self) -> None:
        s = score([
            _req("Q1", "required", Verdict.MET, 2.0),
            _req("Q2", "required", Verdict.PARTIALLY_MET, 1.0),
            _req("Q3", "preferred", Verdict.MET, 1.0),
        ])  # fmt: skip
        assert s.overall == 80.0  # (2 + 1 + 1) / 5
        assert s.fit_label == "strong"
        assert s.coverage == 1.0
        assert (s.required_met, s.required_total, s.preferred_met, s.preferred_total) == (
            1,
            2,
            1,
            1,
        )

    def test_unmet_required_caps_label(self) -> None:
        s = score([
            _req("Q1", "required", Verdict.MET, 2.0),
            _req("Q2", "required", Verdict.MET, 2.0),
            _req("Q3", "required", Verdict.MET, 2.0),
            _req("Q4", "required", Verdict.NOT_MET, 0.0),
        ])  # fmt: skip
        assert s.overall == 75.0
        assert s.fit_label == "moderate"
        assert s.unmet_required == ["Q4"]

    def test_low_coverage_gives_null_not_zero(self) -> None:
        s = score([
            _req("Q1", "required", Verdict.MET, 2.0),
            _req("Q2", "required", Verdict.INSUFFICIENT_EVIDENCE, 0.0),
            _req("Q3", "required", Verdict.INSUFFICIENT_EVIDENCE, 0.0),
        ])  # fmt: skip
        assert s.overall is None
        assert s.fit_label == "insufficient_evidence"
        assert s.coverage == round(2 / 6, 3)
        assert s.unevidenced_required == ["Q2", "Q3"]
        assert s.unmet_required == []

    def test_weak(self) -> None:
        s = score([_req("Q1", "required", Verdict.NOT_MET, 0.0),
                   _req("Q2", "preferred", Verdict.PARTIALLY_MET, 0.5)])  # fmt: skip
        assert s.overall == round(100 * 0.5 / 3, 1)
        assert s.fit_label == "weak"

    def test_empty(self) -> None:
        s = score([])
        assert s.overall is None
        assert s.fit_label == "insufficient_evidence"
