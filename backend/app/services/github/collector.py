"""Collect a GitHub user's public evidence into a :class:`GitHubSnapshot`.

API budget per sync (defaults): 1 profile + up to 3 repository pages + up to 10
READMEs + up to 3 event pages (core limit) and 2 search calls (search limit).
Unauthenticated: 60 core requests/hour and 10 searches/minute; with
GITHUB_TOKEN: 5,000/hour and 30/minute.

Failure model:
* profile-level failure (not found, organisation, rate limit, network) aborts the
  sync with a typed :class:`GitHubError`;
* every other section fails independently and records why (partial snapshot);
* once the core limit is exhausted, remaining core sections are marked
  ``rate_limited`` without spending more calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.enums import AvailabilityStatus, CollectionErrorCode
from app.integrations.github import (
    GitHubClient,
    GitHubError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    segment,
)
from app.schemas.github import (
    ActivitySection,
    AggregateSection,
    ContributionsSection,
    GitHubProfileData,
    GitHubRepository,
    GitHubSnapshot,
    ProfileSection,
    Provenance,
    ReadmeInfo,
    RepositoriesSection,
    Section,
)
from app.services.github.normalize import (
    aggregate,
    normalize_profile,
    normalize_readme,
    normalize_repository,
    normalize_search_item,
    summarize_events,
)

logger = logging.getLogger("app.github.collector")


class NotAUserError(GitHubError):
    code = CollectionErrorCode.NOT_A_USER
    availability = AvailabilityStatus.UNAVAILABLE


@dataclass(frozen=True)
class CollectorLimits:
    max_repo_pages: int = 3
    readme_limit: int = 10
    readme_max_bytes: int = 100_000
    max_event_pages: int = 3
    search_items: int = 10


class GitHubCollector:
    def __init__(self, client: GitHubClient, limits: CollectorLimits) -> None:
        self._client = client
        self._limits = limits

    def _prov(self, api_reference: str, source_url: str | None) -> Provenance:
        return Provenance(
            source_url=source_url, api_reference=api_reference, collected_at=datetime.now(UTC)
        )

    async def collect(self, username: str) -> GitHubSnapshot:
        started = self._client.requests_made
        warnings: list[str] = []
        user = segment(username)

        # ---- profile (required) --------------------------------------------------
        try:
            response = await self._client.get(f"/users/{user}")
        except GitHubNotFoundError as exc:
            raise GitHubNotFoundError(
                f"GitHub user '{username}' does not exist or is not public"
            ) from exc
        try:
            profile = normalize_profile(response.data)
        except ValueError as exc:
            raise GitHubError("GitHub returned an incomplete profile") from exc
        if profile.account_type != "User":
            raise NotAUserError(
                f"'{profile.login}' is a GitHub {profile.account_type.lower()}, "
                "not a personal account"
            )
        login = segment(profile.login)
        profile_section = ProfileSection(
            status=AvailabilityStatus.AVAILABLE,
            provenance=self._prov("GET /users/{username}", profile.html_url),
            data=profile,
        )
        core_limited = False

        # ---- repositories --------------------------------------------------------
        repos_section, core_limited = await self._repositories(login, profile, warnings)

        # ---- READMEs (subset) ----------------------------------------------------
        if repos_section.items and self._limits.readme_limit > 0:
            core_limited = await self._readmes(repos_section.items, warnings, core_limited)

        non_forks = [r for r in repos_section.items if not r.is_fork]
        derived = repos_section.status in (AvailabilityStatus.AVAILABLE, AvailabilityStatus.EMPTY)
        languages = AggregateSection(
            status=(
                AvailabilityStatus.AVAILABLE
                if aggregate([r.primary_language or "" for r in non_forks])
                else AvailabilityStatus.EMPTY
            )
            if derived
            else repos_section.status,
            note=(
                "Primary language of each non-fork public repository "
                "(counts of repositories, not code volume)."
            ),
            provenance=self._prov(
                "derived from GET /users/{username}/repos", f"{profile.html_url}?tab=repositories"
            ),
            items=aggregate([r.primary_language or "" for r in non_forks]) if derived else [],
        )
        topic_values = [t for r in non_forks for t in r.topics]
        topics = AggregateSection(
            status=(AvailabilityStatus.AVAILABLE if topic_values else AvailabilityStatus.EMPTY)
            if derived
            else repos_section.status,
            note="Topics set by the owner on non-fork public repositories.",
            provenance=self._prov(
                "derived from GET /users/{username}/repos", f"{profile.html_url}?tab=repositories"
            ),
            items=aggregate(topic_values) if derived else [],
        )

        # ---- public activity -----------------------------------------------------
        activity = await self._activity(login, profile, warnings, core_limited)

        # ---- authored PRs / issues (search API, separate limit) -------------------
        pull_requests = await self._search(profile, "pr", warnings)
        issues = await self._search(profile, "issue", warnings)

        calendar = Section(
            status=AvailabilityStatus.UNAVAILABLE,
            note=(
                "The contribution calendar is not offered by the GitHub REST API (GraphQL only) "
                "and is not collected; its absence says nothing about the candidate."
            ),
            provenance=self._prov("not requested", f"{profile.html_url}"),
        )
        if not self._client.authenticated:
            warnings.append(
                "Collected without GITHUB_TOKEN (60 requests/hour); set a token for higher limits"
            )

        return GitHubSnapshot(
            collected_at=datetime.now(UTC),
            authenticated=self._client.authenticated,
            profile=profile_section,
            repositories=repos_section,
            languages=languages,
            topics=topics,
            activity=activity,
            pull_requests=pull_requests,
            issues=issues,
            contribution_calendar=calendar,
            warnings=warnings,
            api_requests=self._client.requests_made - started,
        )

    async def _repositories(
        self, login: str, profile: GitHubProfileData, warnings: list[str]
    ) -> tuple[RepositoriesSection, bool]:
        prov = self._prov(
            "GET /users/{username}/repos?type=owner&sort=pushed",
            f"{profile.html_url}?tab=repositories",
        )
        try:
            page = await self._client.get_paginated(
                f"/users/{login}/repos",
                {"type": "owner", "sort": "pushed", "per_page": 100},
                max_pages=self._limits.max_repo_pages,
            )
        except GitHubError as exc:
            warnings.append(f"Repositories could not be collected: {exc.message}")
            return (
                RepositoriesSection(
                    status=exc.availability,
                    note=exc.message,
                    provenance=prov,
                    reported_public_repos=profile.public_repos,
                ),
                isinstance(exc, GitHubRateLimitError),
            )
        warnings.extend(page.warnings)
        items: list[GitHubRepository] = []
        skipped = 0
        for raw in page.items:
            repo = normalize_repository(raw)
            if repo is None:
                skipped += 1
            else:
                items.append(repo)
        if skipped:
            warnings.append(f"Skipped {skipped} repository record(s) with missing fields")
        if page.truncated:
            warnings.append(
                f"Only the {len(items)} most recently pushed repositories were collected "
                f"(limit {self._limits.max_repo_pages * 100})"
            )
        status = AvailabilityStatus.AVAILABLE if items else AvailabilityStatus.EMPTY
        note = (
            None
            if items
            else "No public repositories owned by this account "
            "(this is not evidence of missing skills)."
        )
        return (
            RepositoriesSection(
                status=status,
                note=note,
                provenance=prov,
                items=items,
                reported_public_repos=profile.public_repos,
                truncated=page.truncated,
                skipped_malformed=skipped,
            ),
            False,
        )

    async def _readmes(
        self, repos: list[GitHubRepository], warnings: list[str], core_limited: bool
    ) -> bool:
        candidates = sorted(
            (r for r in repos if not r.is_fork and not r.is_archived),
            key=lambda r: r.pushed_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )[: self._limits.readme_limit]
        for repo in candidates:
            fallback = f"{repo.html_url}#readme"
            if core_limited:
                repo.readme = ReadmeInfo(
                    status=AvailabilityStatus.RATE_LIMITED,
                    source_url=fallback,
                    note="Not collected: GitHub rate limit reached",
                )
                continue
            owner, _, name = repo.full_name.partition("/")
            try:
                response = await self._client.get(f"/repos/{segment(owner)}/{segment(name)}/readme")
            except GitHubNotFoundError:
                repo.readme = ReadmeInfo(
                    status=AvailabilityStatus.EMPTY,
                    source_url=repo.html_url,
                    note="Repository has no README",
                )
                continue
            except GitHubError as exc:
                repo.readme = ReadmeInfo(
                    status=exc.availability, source_url=fallback, note=exc.message
                )
                if isinstance(exc, GitHubRateLimitError):
                    core_limited = True
                    warnings.append(
                        "GitHub rate limit reached while collecting READMEs; some were skipped"
                    )
                continue
            repo.readme = normalize_readme(
                response.data, max_bytes=self._limits.readme_max_bytes, fallback_url=fallback
            )
        return core_limited

    async def _activity(
        self, login: str, profile: GitHubProfileData, warnings: list[str], core_limited: bool
    ) -> ActivitySection:
        prov = self._prov("GET /users/{username}/events/public", f"{profile.html_url}?tab=overview")
        if self._limits.max_event_pages == 0:
            return ActivitySection(
                status=AvailabilityStatus.NOT_COLLECTED,
                note="Disabled by configuration",
                provenance=prov,
            )
        if core_limited:
            return ActivitySection(
                status=AvailabilityStatus.RATE_LIMITED,
                note="Not collected: GitHub rate limit reached",
                provenance=prov,
            )
        try:
            page = await self._client.get_paginated(
                f"/users/{login}/events/public",
                {"per_page": 100},
                max_pages=self._limits.max_event_pages,
            )
        except GitHubError as exc:
            warnings.append(f"Public activity could not be collected: {exc.message}")
            return ActivitySection(status=exc.availability, note=exc.message, provenance=prov)
        warnings.extend(page.warnings)
        return summarize_events(page.items, prov, truncated=page.truncated)

    async def _search(
        self, profile: GitHubProfileData, kind: str, warnings: list[str]
    ) -> ContributionsSection:
        label = "pull requests" if kind == "pr" else "issues"
        query = f"author:{profile.login} type:{kind} is:public"
        tab = "pulls" if kind == "pr" else "issues"
        prov = self._prov(
            f"GET /search/issues?q=author:{{username}}+type:{kind}+is:public",
            f"https://github.com/{tab}?q=author%3A{profile.login}+is%3Apublic",
        )
        if self._limits.search_items == 0:
            return ContributionsSection(
                status=AvailabilityStatus.NOT_COLLECTED,
                note="Disabled by configuration",
                provenance=prov,
            )
        try:
            response = await self._client.get(
                "/search/issues",
                {
                    "q": query,
                    "sort": "created",
                    "order": "desc",
                    "per_page": self._limits.search_items,
                },
            )
        except GitHubError as exc:
            warnings.append(f"Authored {label} could not be collected: {exc.message}")
            return ContributionsSection(status=exc.availability, note=exc.message, provenance=prov)
        data = response.data if isinstance(response.data, dict) else {}
        listed = data.get("items")
        raw_items: list[Any] = listed if isinstance(listed, list) else []
        items = [i for i in (normalize_search_item(r) for r in raw_items) if i is not None]
        total = data.get("total_count") if isinstance(data.get("total_count"), int) else None
        incomplete = data.get("incomplete_results") is True
        if incomplete:
            warnings.append(f"GitHub reported incomplete search results for authored {label}")
        return ContributionsSection(
            status=AvailabilityStatus.AVAILABLE
            if items or (total or 0) > 0
            else AvailabilityStatus.EMPTY,
            note=(
                f"Most recent {len(items)} public {label} authored by the user; "
                "total_count is GitHub's search estimate."
            ),
            provenance=prov,
            total_count=total,
            incomplete_results=incomplete,
            items=items,
        )
