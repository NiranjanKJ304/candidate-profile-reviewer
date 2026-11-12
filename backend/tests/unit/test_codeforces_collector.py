"""Codeforces normalisation, analytics and collection (fake API, no network)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.core.enums import AvailabilityStatus
from app.integrations.codeforces import CodeforcesError, CodeforcesNotFoundError
from app.schemas.codeforces import Submission
from app.services.codeforces.analytics import submission_stats
from app.services.codeforces.collector import CodeforcesCollector, CollectorLimits
from app.services.codeforces.normalize import (
    normalize_rating_change,
    normalize_submission,
    normalize_user,
)
from tests import codeforces_fakes as cf


async def _collect(
    fake: cf.FakeCodeforces | None = None, handle: str = cf.HANDLE, **limits: Any
) -> Any:
    fake = fake or cf.FakeCodeforces()
    return await CodeforcesCollector(fake.client(), CollectorLimits(**limits)).collect(handle)


# --------------------------------------------------------------------------- normalisation
class TestNormalizeUser:
    def test_fields_and_private_data_dropped(self) -> None:
        user = normalize_user(cf.user_payload())
        assert user.handle == cf.HANDLE
        assert (user.first_name, user.last_name) == ("Algo", "Dev")
        assert (user.rating, user.max_rating) == (1650, 1920)
        assert (user.rank, user.max_rank) == ("expert", "candidate master")
        assert user.contribution == 5
        assert user.registered_at == datetime(2017, 7, 14, 2, 40, tzinfo=UTC)
        assert user.profile_url == f"https://codeforces.com/profile/{cf.HANDLE}"
        dumped = user.model_dump_json()
        for private in (cf.PRIVATE_EMAIL, "vk-123", "openid-456", "userpic"):
            assert private not in dumped

    def test_unrated_and_missing_optional_fields(self) -> None:
        user = normalize_user({"handle": "newbie_1", "organization": "  ", "rating": "high"})
        assert user.rating is None
        assert user.rank is None
        assert user.organization is None
        assert user.registered_at is None

    @pytest.mark.parametrize(
        "raw", [None, [], {"handle": ""}, {"handle": "bad handle"}, {"handle": 5}, {}]
    )
    def test_unusable(self, raw: Any) -> None:
        with pytest.raises(ValueError, match="user"):
            normalize_user(raw)

    @pytest.mark.parametrize("value", [-5, 0, 10**12, True, "123", 1.5])
    def test_bad_timestamps_become_none(self, value: Any) -> None:
        assert (
            normalize_user({"handle": "a1", "lastOnlineTimeSeconds": value}).last_online_at is None
        )


class TestNormalizeRating:
    def test_change(self) -> None:
        change = normalize_rating_change(cf.rating_payload()[1])
        assert change is not None
        assert (change.old_rating, change.new_rating, change.delta) == (1600, 1650, 50)
        assert change.contest_url == "https://codeforces.com/contest/1010"
        assert change.rank == 1200

    def test_gym_contest_url(self) -> None:
        change = normalize_rating_change({"contestId": 100500, "oldRating": 0, "newRating": 1})
        assert change is not None
        assert change.contest_url == "https://codeforces.com/gym/100500"

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            {"contestId": 1, "oldRating": 1500},
            {"oldRating": 1, "newRating": 2},
            {"contestId": -1, "oldRating": 1, "newRating": 2},
            {"contestId": "1", "oldRating": 1, "newRating": 2},
        ],
    )
    def test_malformed(self, raw: Any) -> None:
        assert normalize_rating_change(raw) is None


class TestNormalizeSubmission:
    def test_fields(self) -> None:
        sub = normalize_submission(
            cf.submission(1, index="C", participant="CONTESTANT", relative=60)
        )
        assert sub is not None
        assert sub.problem_key == "1010/C"
        assert sub.problem_url == "https://codeforces.com/contest/1010/problem/C"
        assert sub.verdict == "OK"
        assert sub.passed_test_count == 12
        assert sub.relative_time_seconds == 60
        assert sub.participant_type == "CONTESTANT"
        assert sub.tags == ["math"]
        dumped = sub.model_dump_json()
        assert cf.TEAMMATE not in dumped  # team members are not stored
        assert cf.HANDLE not in dumped

    def test_practice_relative_time_is_none(self) -> None:
        sub = normalize_submission(cf.submission(1))
        assert sub is not None
        assert sub.relative_time_seconds is None

    def test_judging_has_no_verdict(self) -> None:
        sub = normalize_submission(cf.submission(1, verdict=None))
        assert sub is not None
        assert sub.verdict is None

    def test_gym_and_problemset_keys(self) -> None:
        gym = normalize_submission(cf.submission(1, contest=100001))
        assert gym is not None
        assert gym.problem_url == "https://codeforces.com/gym/100001/problem/A"
        raw = cf.submission(2)
        del raw["contestId"]
        raw["problem"] = {"problemsetName": "acmsguru", "index": "100", "name": "A+B"}
        sguru = normalize_submission(raw)
        assert sguru is not None
        assert sguru.problem_key == "acmsguru/100"
        assert sguru.problem_url is None

    def test_name_only_key_and_unsafe_index_has_no_url(self) -> None:
        raw = cf.submission(1)
        raw["problem"] = {"name": "Mystery"}
        del raw["contestId"]
        sub = normalize_submission(raw)
        assert sub is not None
        assert sub.problem_key == "name:Mystery"
        raw = cf.submission(2)
        raw["problem"]["index"] = "A/../x"
        sub = normalize_submission(raw)
        assert sub is not None
        assert sub.problem_url is None

    def test_tags_cleaned(self) -> None:
        raw = cf.submission(1, tags=["dp", "dp", " greedy ", 5, ""])  # type: ignore[list-item]
        sub = normalize_submission(raw)
        assert sub is not None
        assert sub.tags == ["dp", "greedy"]

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda r: r.pop("id"),
            lambda r: r.pop("creationTimeSeconds"),
            lambda r: r.update(problem="x"),
            lambda r: r.update(problem={}, contestId=None),
        ],
    )
    def test_malformed(self, mutate: Any) -> None:
        raw = cf.submission(1)
        mutate(raw)
        assert normalize_submission(raw) is None
        assert normalize_submission("nope") is None


# --------------------------------------------------------------------------- analytics
def _subs(raw: list[dict[str, Any]]) -> list[Submission]:
    out = [normalize_submission(r) for r in raw]
    assert all(s is not None for s in out)
    return [s for s in out if s is not None]


class TestAnalytics:
    def test_counts_distinguish_submissions_from_unique_problems(self) -> None:
        stats = submission_stats(_subs(cf.default_submissions()), truncated=False)
        assert stats.scope == "collected_window"
        assert stats.total_submissions == 6
        assert stats.accepted_submissions == 4  # 1010/B accepted twice
        assert stats.accepted_unique_problems == 3
        assert stats.rejected_submissions == 2
        assert stats.pending_submissions == 0
        assert stats.attempted_unique_problems == 4

    def test_difficulty_and_tags_count_each_accepted_problem_once(self) -> None:
        stats = submission_stats(_subs(cf.default_submissions()), truncated=False)
        assert [(b.rating, b.problems) for b in stats.accepted_by_rating] == [
            (1200, 1),
            (1600, 1),
            (None, 1),
        ]
        tags = {t.name: t.count for t in stats.accepted_by_tag}
        assert tags == {"dp": 1, "greedy": 1, "math": 1, "implementation": 1}
        assert "graphs" not in tags  # only rejected

    def test_verdicts_languages_months_contests(self) -> None:
        stats = submission_stats(_subs(cf.default_submissions()), truncated=True)
        assert {v.name: v.count for v in stats.verdicts} == {
            "OK": 4,
            "WRONG_ANSWER": 1,
            "TIME_LIMIT_EXCEEDED": 1,
        }
        assert stats.verdicts[0].name == "OK"
        assert {lang.name: lang.count for lang in stats.languages} == {"Python 3": 4, "C++17": 2}
        assert sum(m.submissions for m in stats.monthly_activity) == 6
        assert sum(m.accepted for m in stats.monthly_activity) == 4
        assert stats.contests_in_window == 1  # only 1010 had CONTESTANT submissions
        assert stats.truncated is True
        assert stats.window_start is not None
        assert stats.window_end is not None
        assert stats.window_start <= stats.window_end

    def test_pending_and_unknown_language(self) -> None:
        raw = [cf.submission(1, verdict=None), cf.submission(2, verdict="TESTING")]
        raw[0]["programmingLanguage"] = None
        stats = submission_stats(_subs(raw), truncated=False)
        assert stats.pending_submissions == 2
        assert stats.rejected_submissions == 0
        assert {v.name for v in stats.verdicts} == {"PENDING", "TESTING"}
        assert {lang.name for lang in stats.languages} == {"Unknown", "Python 3"}

    def test_empty(self) -> None:
        stats = submission_stats([], truncated=False)
        assert stats.total_submissions == 0
        assert stats.accepted_by_rating == []
        assert stats.window_start is None

    def test_reproducible(self) -> None:
        a = submission_stats(_subs(cf.default_submissions()), truncated=False)
        b = submission_stats(list(reversed(_subs(cf.default_submissions()))), truncated=False)
        assert a == b


# --------------------------------------------------------------------------- collector
class TestCollect:
    async def test_full_collection(self) -> None:
        fake = cf.FakeCodeforces()
        snap = await _collect(fake)
        assert fake.methods() == ["user.info", "user.rating", "user.status"]
        assert snap.api_requests == 3
        assert snap.profile.status is AvailabilityStatus.AVAILABLE
        assert snap.rating_history.status is AvailabilityStatus.AVAILABLE
        assert [c.contest_id for c in snap.rating_history.items] == [1000, 1010]  # oldest first
        assert snap.submissions.status is AvailabilityStatus.AVAILABLE
        assert [s.id for s in snap.submissions.items] == [1, 2, 3, 4, 5, 6]  # newest first
        assert snap.submissions.truncated is False
        assert snap.analytics.status is AvailabilityStatus.AVAILABLE
        assert snap.analytics.lifetime.rated_contests == 2
        assert snap.analytics.lifetime.current_rating == 1650
        assert snap.analytics.submissions is not None
        assert snap.analytics.submissions.accepted_unique_problems == 3
        assert snap.warnings == []

    async def test_provenance_on_every_section(self) -> None:
        snap = await _collect()
        methods = {
            "profile": "user.info",
            "rating_history": "user.rating",
            "submissions": "user.status",
        }
        for name, method in methods.items():
            prov = getattr(snap, name).provenance
            assert prov.source_platform == "codeforces"
            assert prov.api_method == method
            assert prov.source_url.startswith("https://codeforces.com/")
            assert prov.collected_at is not None
        assert snap.analytics.provenance.api_method.startswith("derived")

    async def test_no_private_data_in_snapshot(self) -> None:
        dumped = (await _collect()).model_dump_json()
        for private in (cf.PRIVATE_EMAIL, cf.TEAMMATE, "vk-123", "openid-456"):
            assert private not in dumped

    async def test_canonical_handle_used_for_later_calls(self) -> None:
        fake = cf.FakeCodeforces()
        snap = await _collect(fake, handle="ALGO_DEV")
        assert snap.profile.data.handle == cf.HANDLE
        assert fake.requests[1].url.params["handle"] == cf.HANDLE

    async def test_unrated_account_warns(self) -> None:
        user = cf.user_payload()
        for key in ("rating", "maxRating", "rank", "maxRank"):
            user.pop(key)
        snap = await _collect(cf.FakeCodeforces(user=user, rating=[]))
        assert snap.rating_history.status is AvailabilityStatus.EMPTY
        assert snap.analytics.lifetime.rated_contests == 0
        assert snap.analytics.lifetime.current_rating is None
        assert any("unrated" in w for w in snap.warnings)

    async def test_empty_account(self) -> None:
        snap = await _collect(cf.FakeCodeforces(rating=[], submissions=[]))
        assert snap.submissions.status is AvailabilityStatus.EMPTY
        assert snap.analytics.status is AvailabilityStatus.EMPTY
        assert snap.analytics.submissions is not None
        assert snap.analytics.submissions.total_submissions == 0  # genuinely none

    async def test_malformed_records_counted(self) -> None:
        rating = [*cf.rating_payload(), {"contestId": "x"}]
        subs = [*cf.default_submissions(), {"id": 99}]
        snap = await _collect(cf.FakeCodeforces(rating=rating, submissions=subs))
        assert snap.rating_history.skipped_malformed == 1
        assert snap.submissions.skipped_malformed == 1
        assert len(snap.submissions.items) == 6
        assert any("malformed" in w for w in snap.warnings)


class TestLimitsAndPagination:
    async def test_limit_truncates_and_detects_more(self) -> None:
        fake = cf.FakeCodeforces()
        snap = await _collect(fake, submission_limit=4)
        assert len(snap.submissions.items) == 4
        assert snap.submissions.truncated is True
        assert snap.submissions.requested_limit == 4
        assert fake.requests[-1].url.params["count"] == "5"  # one extra to detect more
        assert snap.analytics.submissions.truncated is True
        assert any("most recent 4 submissions" in w for w in snap.warnings)

    async def test_exact_limit_is_not_truncated(self) -> None:
        snap = await _collect(submission_limit=6)
        assert len(snap.submissions.items) == 6
        assert snap.submissions.truncated is False

    async def test_pagination(self) -> None:
        fake = cf.FakeCodeforces()
        snap = await _collect(fake, submission_limit=5, page_size=2)
        status_calls = [r for r in fake.requests if r.url.path.endswith("user.status")]
        assert [(r.url.params["from"], r.url.params["count"]) for r in status_calls] == [
            ("1", "2"),
            ("3", "2"),
            ("5", "2"),
        ]
        assert [s.id for s in snap.submissions.items] == [1, 2, 3, 4, 5]
        assert snap.submissions.truncated is True

    async def test_pagination_stops_on_short_page(self) -> None:
        fake = cf.FakeCodeforces()
        snap = await _collect(fake, submission_limit=100, page_size=4)
        assert len([m for m in fake.methods() if m == "user.status"]) == 2
        assert len(snap.submissions.items) == 6
        assert snap.submissions.truncated is False

    async def test_hard_maximum(self) -> None:
        assert CollectorLimits(submission_limit=5000).effective_limit() == 1000
        assert CollectorLimits(submission_limit=0).effective_limit() == 1
        fake = cf.FakeCodeforces()
        await _collect(fake, submission_limit=5000)
        assert fake.requests[-1].url.params["count"] == "1000"

    async def test_duplicate_submissions_across_pages_are_dropped(self) -> None:
        subs = cf.default_submissions()
        fake = cf.FakeCodeforces(submissions=[subs[0], subs[1], subs[1], subs[2]])
        snap = await _collect(fake, page_size=2)
        assert [s.id for s in snap.submissions.items] == [1, 2, 3]

    async def test_api_ignoring_from_is_bounded(self) -> None:
        fake = cf.FakeCodeforces()
        fake.routes["user.status"] = lambda r: cf.ok(cf.default_submissions()[:2])
        snap = await _collect(fake, submission_limit=10, page_size=2)
        assert len([m for m in fake.methods() if m == "user.status"]) <= 7
        assert snap.submissions.truncated is True


class TestPartialFailures:
    async def test_rating_failure_keeps_other_sections(self) -> None:
        fake = cf.FakeCodeforces()
        fake.routes["user.rating"] = lambda r: cf.failed("Internal Server Error", 500)
        snap = await _collect(fake)
        assert snap.rating_history.status is AvailabilityStatus.ERROR
        assert "could not be collected" in (snap.rating_history.note or "")
        assert snap.analytics.lifetime.rated_contests is None  # unknown, not zero
        assert snap.submissions.status is AvailabilityStatus.AVAILABLE

    async def test_submissions_failure_never_becomes_zero(self) -> None:
        fake = cf.FakeCodeforces()
        fake.routes["user.status"] = lambda r: httpx.Response(503, text="down")
        snap = await _collect(fake)
        assert snap.submissions.status is AvailabilityStatus.NETWORK_ERROR
        assert snap.submissions.items == []
        assert snap.analytics.submissions is None
        assert snap.analytics.status is AvailabilityStatus.NETWORK_ERROR
        assert "not the same as zero" in (snap.analytics.note or "")
        assert snap.analytics.lifetime.current_rating == 1650

    async def test_rate_limit_skips_remaining_calls(self) -> None:
        fake = cf.FakeCodeforces()
        fake.routes["user.rating"] = lambda r: cf.failed("Call limit exceeded", 503)
        snap = await _collect(fake)
        assert snap.rating_history.status is AvailabilityStatus.RATE_LIMITED
        assert snap.submissions.status is AvailabilityStatus.RATE_LIMITED
        assert "user.status" not in fake.methods()
        assert snap.analytics.submissions is None

    async def test_later_page_failure_keeps_collected_items(self) -> None:
        fake = cf.FakeCodeforces()
        original = fake.routes["user.status"]

        def flaky(request: httpx.Request) -> httpx.Response:
            if request.url.params["from"] != "1":
                return cf.failed("Internal problem", 500)
            return original(request)

        fake.routes["user.status"] = flaky
        snap = await _collect(fake, page_size=2)
        assert [s.id for s in snap.submissions.items] == [1, 2]
        assert snap.submissions.truncated is True
        assert any("stopped early" in w for w in snap.warnings)

    @pytest.mark.parametrize("method", ["user.rating", "user.status"])
    async def test_unexpected_payload_shape(self, method: str) -> None:
        fake = cf.FakeCodeforces()
        fake.routes[method] = lambda r: cf.ok({"not": "a list"})
        snap = await _collect(fake)
        section = snap.rating_history if method == "user.rating" else snap.submissions
        assert section.status is AvailabilityStatus.ERROR


class TestProfileFailures:
    async def test_not_found(self) -> None:
        with pytest.raises(CodeforcesNotFoundError, match="does not exist"):
            await _collect(handle="nobody_here")

    @pytest.mark.parametrize("result", [[], None, [{"handle": ""}], "x"])
    async def test_incomplete_user(self, result: Any) -> None:
        fake = cf.FakeCodeforces()
        fake.routes["user.info"] = lambda r: cf.ok(result)
        with pytest.raises(CodeforcesError):
            await _collect(fake)
        assert fake.methods() == ["user.info"]

    async def test_profile_rate_limit_aborts(self) -> None:
        fake = cf.FakeCodeforces()
        fake.routes["user.info"] = lambda r: cf.failed("Call limit exceeded", 503)
        with pytest.raises(CodeforcesError) as info:
            await _collect(fake)
        assert info.value.availability is AvailabilityStatus.RATE_LIMITED
