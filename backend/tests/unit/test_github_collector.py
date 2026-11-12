"""GitHub normalisation and collection: full, partial, empty, malformed, private data."""

from __future__ import annotations

import base64
from datetime import UTC, datetime

import httpx
import pytest

from app.core.enums import AvailabilityStatus, CollectionErrorCode
from app.integrations.github import GitHubError, GitHubNotFoundError, GitHubRateLimitError
from app.schemas.github import GitHubSnapshot, Provenance
from app.services.github.collector import CollectorLimits, GitHubCollector, NotAUserError
from app.services.github.normalize import (
    aggregate,
    normalize_profile,
    normalize_readme,
    normalize_repository,
    normalize_search_item,
    summarize_events,
)
from tests import github_fakes as gf

LIMITS = CollectorLimits(
    max_repo_pages=3, readme_limit=10, readme_max_bytes=100_000, max_event_pages=3, search_items=10
)
PROV = Provenance(api_reference="test", collected_at=datetime.now(UTC))


async def _collect(
    fake: gf.FakeGitHub, limits: CollectorLimits = LIMITS, token: str | None = None
) -> GitHubSnapshot:
    return await GitHubCollector(fake.client(token=token), limits).collect(gf.LOGIN)


class TestNormalize:
    def test_profile_drops_email(self) -> None:
        p = normalize_profile(gf.user_payload())
        assert p.login == gf.LOGIN
        assert p.id == gf.USER_ID
        assert p.public_repos == 3
        assert "email" not in p.model_dump()
        assert gf.PRIVATE_EMAIL not in p.model_dump_json()

    def test_profile_missing_identity_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="login"):
            normalize_profile({"name": "x"})
        with pytest.raises(ValueError, match="login"):
            normalize_profile(["not", "a", "dict"])

    def test_profile_malformed_optional_fields_become_null(self) -> None:
        p = normalize_profile(
            gf.user_payload(
                followers="many",
                created_at="yesterday",
                bio=42,
                public_repos=-1,
                html_url="https://evil.example/x",
            )
        )
        assert p.followers is None
        assert p.created_at is None
        assert p.bio is None
        assert p.public_repos is None
        assert p.html_url == f"https://github.com/{gf.LOGIN}"

    def test_repository(self) -> None:
        r = normalize_repository(gf.repo("data-api"))
        assert r is not None
        assert r.primary_language == "Python"
        assert r.topics == ["fastapi", "postgresql"]
        assert r.is_fork is False

    @pytest.mark.parametrize(
        "raw",
        [
            {"name": "x"},
            gf.repo("x", html_url="https://evil.example/x"),
            gf.repo("x", private=True),
            "not a dict",
            None,
        ],
    )
    def test_unusable_repository_records_are_skipped(self, raw: object) -> None:
        assert normalize_repository(raw) is None

    def test_repository_bad_topics_and_counts(self) -> None:
        r = normalize_repository(
            gf.repo("x", topics="python", stargazers_count=True, forks_count=None)
        )
        assert r is not None
        assert r.topics == []
        assert r.stargazers_count is None

    def test_readme_decoding_and_limits(self) -> None:
        ok = normalize_readme(gf.readme_payload(), max_bytes=1000, fallback_url="u")
        assert ok.status is AvailabilityStatus.AVAILABLE
        assert ok.excerpt is not None
        assert "FastAPI" in ok.excerpt
        big = normalize_readme(gf.readme_payload(size=10**6), max_bytes=1000, fallback_url="u")
        assert big.truncated is True
        assert big.excerpt is None
        long_text = normalize_readme(
            gf.readme_payload("x" * 5000), max_bytes=100_000, fallback_url="u"
        )
        assert long_text.truncated is True
        assert long_text.excerpt is not None
        assert len(long_text.excerpt) == 3000
        empty = normalize_readme(gf.readme_payload("   "), max_bytes=1000, fallback_url="u")
        assert empty.status is AvailabilityStatus.EMPTY
        odd = normalize_readme(
            {"encoding": "utf-8", "content": "x"}, max_bytes=1000, fallback_url="u"
        )
        assert odd.status is AvailabilityStatus.ERROR

    def test_readme_with_binary_content_is_safe(self) -> None:
        payload = {
            "encoding": "base64",
            "content": base64.b64encode(bytes(range(256))).decode(),
            "size": 256,
        }
        info = normalize_readme(payload, max_bytes=1000, fallback_url="u")
        assert info.status is AvailabilityStatus.AVAILABLE

    def test_events_keep_counts_not_messages_or_emails(self) -> None:
        section = summarize_events(gf.events_payload(), PROV, truncated=False)
        assert section.events_analyzed == 3
        assert section.push_events == 1
        assert section.commits_reported == 2
        assert section.event_counts["PullRequestEvent"] == 1
        assert section.active_repositories[0] in (f"{gf.LOGIN}/data-api", "other/project")
        dumped = section.model_dump_json()
        assert gf.PRIVATE_EMAIL not in dumped
        assert gf.COMMIT_MESSAGE not in dumped

    def test_events_without_commit_counts(self) -> None:
        events = [
            {
                "type": "PushEvent",
                "created_at": "2026-01-01T00:00:00Z",
                "repo": {"name": "a/b"},
                "payload": {},
            }
        ]
        section = summarize_events(events, PROV, truncated=False)
        assert section.push_events == 1
        assert section.commits_reported is None  # unknown, not zero

    def test_empty_and_malformed_events(self) -> None:
        assert summarize_events([], PROV, truncated=False).status is AvailabilityStatus.EMPTY
        assert (
            summarize_events([None, {"type": 3}, "x"], PROV, truncated=False).events_analyzed == 0
        )

    def test_search_item(self) -> None:
        item = normalize_search_item(gf.search_payload("pr")["items"][0])
        assert item is not None
        assert item.repository == "other/project"
        assert item.merged is True
        assert normalize_search_item(gf.search_payload("issue")["items"][0]).merged is None  # type: ignore[union-attr]
        assert normalize_search_item({"title": "x"}) is None

    def test_aggregate_orders_by_count_then_name(self) -> None:
        assert [
            (s.name, s.repositories) for s in aggregate(["Go", "Python", "python", "Python", ""])
        ] == [("Python", 2), ("Go", 1), ("python", 1)]


class TestCollector:
    async def test_full_collection(self) -> None:
        fake = gf.FakeGitHub()
        snap = await _collect(fake)
        assert snap.profile.status is AvailabilityStatus.AVAILABLE
        assert snap.profile.data is not None
        assert snap.profile.data.login == gf.LOGIN
        assert [r.name for r in snap.repositories.items] == [
            "data-api",
            "ml-notebooks",
            "forked-lib",
            "old-site",
        ]
        assert snap.repositories.reported_public_repos == 3
        # READMEs only for non-fork, non-archived repositories
        by_name = {r.name: r for r in snap.repositories.items}
        assert by_name["data-api"].readme is not None
        assert by_name["data-api"].readme.status is AvailabilityStatus.AVAILABLE
        assert by_name["forked-lib"].readme is None
        assert by_name["old-site"].readme is None
        # languages/topics exclude forks
        assert [s.name for s in snap.languages.items] == [
            "JavaScript",
            "Jupyter Notebook",
            "Python",
        ]
        assert "go" not in [s.name for s in snap.topics.items]
        assert snap.activity.commits_reported == 2
        assert snap.pull_requests.total_count == 1
        assert snap.issues.items[0].merged is None
        assert snap.contribution_calendar.status is AvailabilityStatus.UNAVAILABLE
        assert snap.authenticated is False
        assert any("GITHUB_TOKEN" in w for w in snap.warnings)
        assert snap.api_requests == len(fake.requests)

    async def test_every_section_has_provenance(self) -> None:
        snap = await _collect(gf.FakeGitHub())
        for name in (
            "profile",
            "repositories",
            "languages",
            "topics",
            "activity",
            "pull_requests",
            "issues",
            "contribution_calendar",
        ):
            prov = getattr(snap, name).provenance
            assert prov.source_platform == "github"
            assert prov.api_reference
            assert prov.collected_at is not None

    async def test_private_data_never_in_snapshot(self) -> None:
        dumped = (await _collect(gf.FakeGitHub())).model_dump_json()
        assert gf.PRIVATE_EMAIL not in dumped
        assert gf.COMMIT_MESSAGE not in dumped

    async def test_empty_repositories_are_not_missing_skills(self) -> None:
        snap = await _collect(gf.FakeGitHub(repos=[]))
        assert snap.repositories.status is AvailabilityStatus.EMPTY
        assert snap.repositories.note is not None
        assert "not evidence of missing skills" in snap.repositories.note
        assert snap.languages.status is AvailabilityStatus.EMPTY

    async def test_malformed_repositories_are_counted(self) -> None:
        snap = await _collect(gf.FakeGitHub(repos=[gf.repo("good"), {"name": "broken"}, "junk"]))
        assert [r.name for r in snap.repositories.items] == ["good"]
        assert snap.repositories.skipped_malformed == 2
        assert any("Skipped 2" in w for w in snap.warnings)

    async def test_missing_readme_is_empty_not_error(self) -> None:
        fake = gf.FakeGitHub()
        fake.routes[f"/repos/{gf.LOGIN}/data-api/readme"] = lambda r: httpx.Response(404, json={})
        snap = await _collect(fake)
        readme = snap.repositories.items[0].readme
        assert readme is not None
        assert readme.status is AvailabilityStatus.EMPTY

    async def test_readme_limit(self) -> None:
        fake = gf.FakeGitHub()
        snap = await _collect(fake, CollectorLimits(readme_limit=1))
        assert sum(1 for p in fake.paths() if p.endswith("/readme")) == 1
        assert snap.repositories.items[0].readme is not None

    async def test_rate_limit_mid_collection_gives_partial_snapshot(self) -> None:
        fake = gf.FakeGitHub()

        def limited(r: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"message": "rate limit"}, headers=gf.rate_headers(0))

        fake.routes[f"/repos/{gf.LOGIN}/data-api/readme"] = limited
        fake.routes[f"/users/{gf.LOGIN}/events/public"] = limited
        snap = await _collect(fake)
        assert snap.profile.status is AvailabilityStatus.AVAILABLE
        assert snap.repositories.status is AvailabilityStatus.AVAILABLE
        assert snap.repositories.items[0].readme.status is AvailabilityStatus.RATE_LIMITED  # type: ignore[union-attr]
        assert snap.repositories.items[1].readme.status is AvailabilityStatus.RATE_LIMITED  # type: ignore[union-attr]
        assert snap.activity.status is AvailabilityStatus.RATE_LIMITED
        assert not any(
            p.endswith("/events/public") for p in fake.paths()
        )  # not attempted once limited
        assert any("rate limit" in w for w in snap.warnings)

    async def test_repository_failure_is_isolated(self) -> None:
        fake = gf.FakeGitHub()
        fake.routes[f"/users/{gf.LOGIN}/repos"] = lambda r: httpx.Response(500)
        snap = await _collect(fake)
        assert snap.repositories.status is AvailabilityStatus.ERROR
        assert snap.languages.status is AvailabilityStatus.ERROR  # derived: unknown, not empty
        assert snap.activity.status is AvailabilityStatus.AVAILABLE

    async def test_search_unavailable_and_incomplete(self) -> None:
        fake = gf.FakeGitHub()
        fake.routes["/search/issues"] = lambda r: (
            httpx.Response(200, json={"total_count": 5, "incomplete_results": True, "items": []})
            if "type:pr" in r.url.params["q"]
            else httpx.Response(422, json={})
        )
        snap = await _collect(fake)
        assert snap.pull_requests.status is AvailabilityStatus.AVAILABLE
        assert snap.pull_requests.incomplete_results is True
        assert snap.issues.status is AvailabilityStatus.UNAVAILABLE

    async def test_search_query_uses_validated_login(self) -> None:
        fake = gf.FakeGitHub()
        await _collect(fake)
        queries = [r.url.params["q"] for r in fake.requests if r.url.path == "/search/issues"]
        assert queries == [
            f"author:{gf.LOGIN} type:pr is:public",
            f"author:{gf.LOGIN} type:issue is:public",
        ]

    async def test_profile_not_found(self) -> None:
        fake = gf.FakeGitHub()
        fake.routes[f"/users/{gf.LOGIN}"] = lambda r: httpx.Response(404, json={})
        with pytest.raises(GitHubNotFoundError, match="does not exist"):
            await _collect(fake)

    async def test_organisation_is_not_a_user(self) -> None:
        with pytest.raises(NotAUserError) as info:
            await _collect(gf.FakeGitHub(user=gf.user_payload(type="Organization")))
        assert info.value.code is CollectionErrorCode.NOT_A_USER

    async def test_profile_rate_limited_aborts(self) -> None:
        fake = gf.FakeGitHub()
        fake.routes[f"/users/{gf.LOGIN}"] = lambda r: httpx.Response(
            403, json={}, headers=gf.rate_headers(0)
        )
        with pytest.raises(GitHubRateLimitError):
            await _collect(fake)
        assert len(fake.requests) == 1

    async def test_incomplete_profile_payload(self) -> None:
        fake = gf.FakeGitHub()
        fake.routes[f"/users/{gf.LOGIN}"] = lambda r: httpx.Response(200, json={"name": "no login"})
        with pytest.raises(GitHubError, match="incomplete profile"):
            await _collect(fake)

    async def test_authenticated_collection_has_no_token_warning(self) -> None:
        snap = await _collect(gf.FakeGitHub(), token="ghp_SyntheticTestToken000000000000000000")
        assert snap.authenticated is True
        assert not any("GITHUB_TOKEN" in w for w in snap.warnings)
        assert "ghp_" not in snap.model_dump_json()

    async def test_configuration_can_disable_activity_and_search(self) -> None:
        fake = gf.FakeGitHub()
        snap = await _collect(
            fake, CollectorLimits(max_event_pages=0, search_items=0, readme_limit=0)
        )
        assert snap.activity.status is AvailabilityStatus.NOT_COLLECTED
        assert snap.pull_requests.status is AvailabilityStatus.NOT_COLLECTED
        assert fake.paths() == [f"/users/{gf.LOGIN}", f"/users/{gf.LOGIN}/repos"]
