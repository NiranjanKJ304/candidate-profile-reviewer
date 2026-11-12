"""Self-reported LeetCode evidence: validation, normalisation, provenance, partial data,
and the guarantee that this integration never contacts LeetCode."""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.enums import AvailabilityStatus
from app.schemas.leetcode import LeetCodeEvidence, LeetCodeEvidenceInput
from app.services.leetcode.evidence import (
    NOT_PROVIDED,
    PARTIAL,
    SELF_REPORTED_WARNING,
    build_evidence,
    evidence_age_days,
)
from app.services.leetcode.feasibility import FINDINGS, SOURCES

URL = "https://leetcode.com/u/jane_doe/"
APP = Path(__file__).resolve().parents[2] / "app"
LEETCODE_FILES = (
    *sorted((APP / "services" / "leetcode").glob("*.py")),
    APP / "api" / "v1" / "leetcode.py",
    APP / "schemas" / "leetcode.py",
)


def _input(**kw: Any) -> LeetCodeEvidenceInput:
    kw.setdefault("reported_by", "candidate")
    return LeetCodeEvidenceInput(**kw)


def full() -> dict[str, Any]:
    return {
        "reported_by": "candidate",
        "as_of": "2026-11-01",
        "total_solved": 300,
        "easy_solved": 150,
        "medium_solved": 120,
        "hard_solved": 30,
        "contest_rating": 1750,
        "contests_attended": 12,
        "contest_top_percentage": 15.5,
        "contest_history": [
            {"contest_name": "Weekly Contest 400", "rank": 1234, "rating_after": 1750,
             "contest_date": "2026-10-20"},
        ],
        "languages": [{"name": "Python3", "problems_solved": 250}, {"name": "C++"}],
        "topics": [{"name": "Dynamic Programming", "problems_solved": 60}],
        "notes": "Figures copied from my profile page.",
    }  # fmt: skip


class TestInputValidation:
    def test_full_payload(self) -> None:
        payload = LeetCodeEvidenceInput(**full())
        assert payload.total_solved == 300
        assert payload.contest_history[0].contest_date == date(2026, 10, 20)

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({}, "at least one evidence value"),
            ({"total_solved": 10, "easy_solved": 5, "medium_solved": 6}, "cannot exceed total"),
            (
                {"total_solved": 10, "easy_solved": 1, "medium_solved": 1, "hard_solved": 1},
                "must equal total",
            ),
            (
                {"total_solved": 5, "languages": [{"name": "Go", "problems_solved": 6}]},
                "cannot exceed",
            ),
            (
                {"total_solved": 5, "topics": [{"name": "Graph", "problems_solved": 9}]},
                "cannot exceed",
            ),
            ({"contests_attended": 0, "contest_rating": 1500}, "contests attended is 0"),
            ({"contests_attended": 0, "contest_top_percentage": 5}, "contests attended is 0"),
            (
                {
                    "contests_attended": 1,
                    "contest_history": [{"contest_name": "A"}, {"contest_name": "B"}],
                },
                "more entries than contests attended",
            ),
            ({"languages": [{"name": "Python"}, {"name": " python "}]}, "duplicate"),
            ({"topics": [{"name": "DP"}, {"name": "dp"}]}, "duplicate"),
            ({"contest_history": [{"contest_name": "W1"}, {"contest_name": "W1"}]}, "duplicate"),
            ({"total_solved": -1}, "greater than or equal"),
            ({"total_solved": 10_001}, "less than or equal"),
            ({"contest_rating": 9999}, "less than or equal"),
            ({"contest_top_percentage": 0}, "greater than"),
            ({"contest_top_percentage": 101}, "less than or equal"),
            ({"as_of": "2014-12-31", "total_solved": 1}, "must not be before"),
            ({"total_solved": 1, "languages": [{"name": "   "}]}, "blank"),
            ({"total_solved": 1, "languages": [{"name": "Py" + chr(7)}]}, "control characters"),
            ({"total_solved": 1, "notes": "x" * 1001}, "at most 1000"),
            ({"total_solved": 1, "notes": "ok" + chr(0)}, "control characters"),
            ({"total_solved": 1, "languages": [{"name": "x"}] * 31}, "at most 30"),
            ({"total_solved": 1, "unexpected": True}, "Extra inputs"),
            ({"total_solved": True}, "valid integer"),
            ({"contest_top_percentage": True}, "valid number"),
            ({"contest_rating": "1500"}, "valid integer"),
        ],
    )
    def test_rejected(self, overrides: dict[str, Any], message: str) -> None:
        with pytest.raises(ValidationError, match=message):
            _input(**overrides)

    def test_future_dates_rejected(self) -> None:
        tomorrow = (datetime.now(UTC).date() + timedelta(days=2)).isoformat()
        with pytest.raises(ValidationError, match="future"):
            _input(as_of=tomorrow, total_solved=1)
        with pytest.raises(ValidationError, match="future"):
            _input(contest_history=[{"contest_name": "W", "contest_date": tomorrow}])

    def test_today_in_a_timezone_ahead_of_utc_is_accepted(self) -> None:
        local_today = (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
        assert _input(as_of=local_today, total_solved=1).as_of is not None

    @pytest.mark.parametrize("reported_by", ["leetcode", "verified", "", None])
    def test_reported_by_is_required_and_limited(self, reported_by: Any) -> None:
        with pytest.raises(ValidationError):
            LeetCodeEvidenceInput(reported_by=reported_by, total_solved=1)

    def test_names_and_notes_are_normalised(self) -> None:
        payload = _input(
            languages=[{"name": "  Python   3 "}], notes="  line one\nline two  ", total_solved=3
        )
        assert payload.languages[0].name == "Python 3"
        assert payload.notes == "line one\nline two"
        assert _input(total_solved=1, notes="   ").notes is None

    def test_zero_is_a_real_value(self) -> None:
        payload = _input(total_solved=0, easy_solved=0, medium_solved=0, hard_solved=0)
        assert payload.total_solved == 0


class TestBuildEvidence:
    def test_labels_and_provenance_on_every_section(self) -> None:
        now = datetime(2026, 11, 13, 9, tzinfo=UTC)
        ev = build_evidence(LeetCodeEvidenceInput(**full()), claimed_profile_url=URL, now=now)
        assert ev.source_type == "self_reported"
        assert ev.verification_status == "not_verified"
        assert ev.submitted_at == now
        assert ev.revision == 1
        for name in ("solved", "contest", "contest_history", "languages", "topics"):
            section = getattr(ev, name)
            assert section.status is AvailabilityStatus.AVAILABLE
            prov = section.provenance
            assert prov.source_platform == "leetcode"
            assert prov.source_type == "self_reported"
            assert prov.verification_status == "not_verified"
            assert prov.method == "manual_entry"
            assert prov.reported_by == "candidate"
            assert prov.claimed_profile_url == URL
            assert prov.submitted_at == now
        assert SELF_REPORTED_WARNING in ev.warnings

    def test_not_provided_is_unknown_not_zero(self) -> None:
        ev = build_evidence(_input(total_solved=42), claimed_profile_url=URL)
        assert ev.solved.total == 42
        assert ev.solved.easy is None
        assert ev.solved.note == PARTIAL
        for name in ("contest", "contest_history", "languages", "topics"):
            section = getattr(ev, name)
            assert section.status is AvailabilityStatus.NOT_COLLECTED
            assert section.note == NOT_PROVIDED
        assert ev.contest.rating is None
        assert ev.contest.attended is None
        assert any("'as of'" in w for w in ev.warnings)

    def test_zero_values_are_kept_as_zero(self) -> None:
        ev = build_evidence(_input(contests_attended=0), claimed_profile_url=URL)
        assert ev.contest.status is AvailabilityStatus.AVAILABLE
        assert ev.contest.attended == 0
        assert ev.solved.status is AvailabilityStatus.NOT_COLLECTED
        assert ev.solved.total is None

    def test_recruiter_entry_and_revision(self) -> None:
        ev = build_evidence(
            _input(reported_by="recruiter", total_solved=1),
            claimed_profile_url=None,
            previous_revision=4,
        )
        assert ev.revision == 5
        assert ev.solved.provenance.reported_by == "recruiter"
        assert ev.solved.provenance.claimed_profile_url is None

    def test_round_trip_and_strict_schema(self) -> None:
        ev = build_evidence(LeetCodeEvidenceInput(**full()), claimed_profile_url=URL)
        dumped = ev.model_dump(mode="json")
        assert LeetCodeEvidence.model_validate(dumped) == ev
        with pytest.raises(ValidationError):
            LeetCodeEvidence.model_validate({**dumped, "verification_status": "verified"})
        with pytest.raises(ValidationError):
            LeetCodeEvidence.model_validate({**dumped, "source_type": "platform_api"})
        with pytest.raises(ValidationError):
            LeetCodeEvidence.model_validate({**dumped, "extra": 1})

    def test_age(self) -> None:
        ev = build_evidence(_input(total_solved=1, as_of="2026-10-01"), claimed_profile_url=URL)
        assert evidence_age_days(ev, today=date(2026, 11, 1)) == 31
        no_date = build_evidence(
            _input(total_solved=1), claimed_profile_url=URL, now=datetime(2026, 11, 1, tzinfo=UTC)
        )
        assert evidence_age_days(no_date, today=date(2026, 11, 11)) == 10
        assert evidence_age_days(no_date, today=date(2026, 10, 1)) == 0


class TestNeverContactsLeetCode:
    """Structural guarantees: the LeetCode modules cannot make network requests."""

    @pytest.mark.parametrize("path", LEETCODE_FILES, ids=lambda p: p.name)
    def test_no_http_client_imports(self, path: Path) -> None:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
                imported.add(node.module)
        forbidden = {"httpx", "requests", "urllib3", "aiohttp", "socket", "http", "urllib.request"}
        assert not imported & forbidden, imported & forbidden
        assert "app.integrations.http" not in imported

    def test_no_leetcode_endpoint_anywhere_in_app(self) -> None:
        for path in APP.rglob("*.py"):
            text = path.read_text(encoding="utf-8").lower()
            assert "leetcode.com/graphql" not in text, path
            assert "leetcode.com/api" not in text, path
            assert "leetcode_session" not in text, path

    def test_feasibility_findings_cite_primary_sources(self) -> None:
        assert SOURCES == ("https://leetcode.com/terms/", "https://leetcode.com/robots.txt")
        joined = " ".join(FINDINGS)
        assert "no official" in joined.lower()
        assert "scraping" in joined
        assert "/graphql" in joined
