"""Deterministic score from per-requirement verdicts. The LLM never produces the number.

Method (stored with every result):

* weight: required 2, preferred 1;
* verdict points: met 1, partially met 0.5, not met 0, insufficient evidence 0;
* trust factor: 0.75 when a met/partial verdict is supported only by self-reported
  evidence, otherwise 1;
* overall = 100 x sum(weight x points x factor) / sum(weight);
* coverage = share of weight whose verdict is not "insufficient evidence". Below
  50% the overall score is **null** (too little could be judged); it is never shown
  as a low number;
* fit label: >= 75 strong, >= 50 moderate, otherwise weak; capped at "moderate"
  when any required requirement is not met.
"""

from __future__ import annotations

from app.core.enums import EvidenceTrust, Verdict
from app.schemas.evaluation import AssessedRequirement, EvidenceItem, FitLabel, ScoreBreakdown

WEIGHTS = {"required": 2.0, "preferred": 1.0}
POINTS = {
    Verdict.MET: 1.0,
    Verdict.PARTIALLY_MET: 0.5,
    Verdict.NOT_MET: 0.0,
    Verdict.INSUFFICIENT_EVIDENCE: 0.0,
}
SELF_REPORTED_FACTOR = 0.75
MIN_COVERAGE = 0.5
METHOD = (
    "weights required=2, preferred=1; points met=1, partially_met=0.5, not_met=0, "
    "insufficient_evidence=0; x0.75 when supported only by self-reported evidence; "
    "score = 100 x credit / total weight; null when coverage < 50%; "
    "label strong >= 75, moderate >= 50, else weak; "
    "capped at moderate if a required item is not met"
)


def trust_factor(evidence_ids: list[str], evidence: dict[str, EvidenceItem]) -> float:
    trusts = {evidence[i].trust for i in evidence_ids if i in evidence}
    if trusts and trusts == {EvidenceTrust.SELF_REPORTED}:
        return SELF_REPORTED_FACTOR
    return 1.0


def credit_for(verdict: Verdict, weight: float, factor: float) -> float:
    return round(weight * POINTS[verdict] * (factor if POINTS[verdict] > 0 else 1.0), 4)


def score(requirements: list[AssessedRequirement]) -> ScoreBreakdown:
    total = sum(r.weight for r in requirements)
    judged = sum(r.weight for r in requirements if r.verdict is not Verdict.INSUFFICIENT_EVIDENCE)
    coverage = round(judged / total, 3) if total else 0.0
    overall = round(100 * sum(r.credit for r in requirements) / total, 1) if total else None
    if coverage < MIN_COVERAGE:
        overall = None
    unmet = [r.id for r in requirements if r.kind == "required" and r.verdict is Verdict.NOT_MET]
    unevidenced = [
        r.id
        for r in requirements
        if r.kind == "required" and r.verdict is Verdict.INSUFFICIENT_EVIDENCE
    ]

    label: FitLabel
    if overall is None:
        label = "insufficient_evidence"
    elif overall >= 75:
        label = "strong"
    elif overall >= 50:
        label = "moderate"
    else:
        label = "weak"
    if unmet and label == "strong":
        label = "moderate"

    def count(kind: str, met_only: bool) -> int:
        return sum(
            1 for r in requirements if r.kind == kind and (not met_only or r.verdict is Verdict.MET)
        )

    return ScoreBreakdown(
        overall=overall,
        fit_label=label,
        coverage=coverage,
        required_met=count("required", True),
        required_total=count("required", False),
        preferred_met=count("preferred", True),
        preferred_total=count("preferred", False),
        unmet_required=unmet,
        unevidenced_required=unevidenced,
        method=METHOD,
    )
