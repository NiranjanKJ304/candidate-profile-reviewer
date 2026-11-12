"""Normalise raw GitHub API payloads into evidence models.

Every function tolerates missing or malformed fields: a wrong-typed optional
field becomes ``None``; an item missing its identity fields is skipped (and
counted) rather than guessed. E-mail addresses and commit messages are never
copied out of API payloads.
"""

from __future__ import annotations

import base64
import binascii
from collections import Counter
from datetime import datetime
from typing import Any

from app.core.enums import AvailabilityStatus
from app.schemas.github import (
    ActivitySection,
    ContributionItem,
    CountStat,
    GitHubProfileData,
    GitHubRepository,
    Provenance,
    ReadmeInfo,
)

README_EXCERPT_CHARS = 3000
MAX_TOPICS = 30


def _str(raw: Any, key: str, max_len: int = 2000) -> str | None:
    value = raw.get(key) if isinstance(raw, dict) else None
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:max_len] or None


def _int(raw: Any, key: str) -> int | None:
    value = raw.get(key) if isinstance(raw, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _bool(raw: Any, key: str) -> bool:
    return raw.get(key) is True if isinstance(raw, dict) else False


def _dt(raw: Any, key: str) -> datetime | None:
    value = raw.get(key) if isinstance(raw, dict) else None
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _github_html(url: str | None) -> str | None:
    return url if url and url.startswith("https://github.com/") else None


def normalize_profile(raw: Any) -> GitHubProfileData:
    """Raise ValueError when identity fields are missing (the profile is unusable)."""
    login = _str(raw, "login", 39)
    user_id = _int(raw, "id")
    if not login or user_id is None:
        raise ValueError("GitHub profile payload has no login/id")
    return GitHubProfileData(
        login=login,
        id=user_id,
        account_type=_str(raw, "type", 40) or "Unknown",
        name=_str(raw, "name", 300),
        bio=_str(raw, "bio", 1000),
        company=_str(raw, "company", 300),
        location=_str(raw, "location", 300),
        blog=_str(raw, "blog", 500),
        public_repos=_int(raw, "public_repos"),
        followers=_int(raw, "followers"),
        following=_int(raw, "following"),
        created_at=_dt(raw, "created_at"),
        updated_at=_dt(raw, "updated_at"),
        html_url=_github_html(_str(raw, "html_url", 500)) or f"https://github.com/{login}",
    )


def normalize_repository(raw: Any) -> GitHubRepository | None:
    name = _str(raw, "name", 200)
    full_name = _str(raw, "full_name", 300)
    html_url = _github_html(_str(raw, "html_url", 500))
    if not (name and full_name and html_url):
        return None
    if raw.get("private") is True:  # never keep private repositories
        return None
    topics_raw = raw.get("topics")
    topics = (
        [t for t in topics_raw if isinstance(t, str)][:MAX_TOPICS]
        if isinstance(topics_raw, list)
        else []
    )
    return GitHubRepository(
        name=name,
        full_name=full_name,
        description=_str(raw, "description", 1000),
        primary_language=_str(raw, "language", 100),
        html_url=html_url,
        created_at=_dt(raw, "created_at"),
        updated_at=_dt(raw, "updated_at"),
        pushed_at=_dt(raw, "pushed_at"),
        stargazers_count=_int(raw, "stargazers_count"),
        forks_count=_int(raw, "forks_count"),
        is_fork=_bool(raw, "fork"),
        is_archived=_bool(raw, "archived"),
        topics=topics,
    )


def normalize_readme(raw: Any, *, max_bytes: int, fallback_url: str) -> ReadmeInfo:
    """Decode a README payload (base64 JSON) into a bounded text excerpt."""
    source = _github_html(_str(raw, "html_url", 500)) or fallback_url
    size = _int(raw, "size")
    if size is not None and size > max_bytes:
        return ReadmeInfo(
            status=AvailabilityStatus.AVAILABLE,
            size_bytes=size,
            truncated=True,
            source_url=source,
            note=f"README larger than {max_bytes} bytes; content not stored",
        )
    content = raw.get("content") if isinstance(raw, dict) else None
    if not isinstance(content, str) or raw.get("encoding") != "base64":
        return ReadmeInfo(
            status=AvailabilityStatus.ERROR, source_url=source, note="Unexpected README format"
        )
    try:
        data = base64.b64decode(content, validate=False)
    except (binascii.Error, ValueError):
        return ReadmeInfo(
            status=AvailabilityStatus.ERROR, source_url=source, note="README could not be decoded"
        )
    text = data[:max_bytes].decode("utf-8", errors="replace").strip()
    if not text:
        return ReadmeInfo(
            status=AvailabilityStatus.EMPTY,
            size_bytes=len(data),
            source_url=source,
            note="README is empty",
        )
    return ReadmeInfo(
        status=AvailabilityStatus.AVAILABLE,
        excerpt=text[:README_EXCERPT_CHARS],
        size_bytes=len(data),
        truncated=len(text) > README_EXCERPT_CHARS,
        source_url=source,
    )


def aggregate(values: list[str]) -> list[CountStat]:
    counts = Counter(v for v in values if v)
    return [
        CountStat(name=k, repositories=n)
        for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    ]


def summarize_events(
    raw_events: list[Any], provenance: Provenance, *, truncated: bool
) -> ActivitySection:
    """Counts only: no commit messages, no author names/e-mails are retained."""
    counts: Counter[str] = Counter()
    repos: Counter[str] = Counter()
    commits = 0
    commits_known = False
    push_events = 0
    times: list[datetime] = []
    for event in raw_events:
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if not isinstance(kind, str):
            continue
        counts[kind] += 1
        repo = event.get("repo")
        repo_name = repo.get("name") if isinstance(repo, dict) else None
        if isinstance(repo_name, str):
            repos[repo_name] += 1
        when = _dt(event, "created_at")
        if when:
            times.append(when)
        if kind == "PushEvent":
            push_events += 1
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            size = payload.get("size") if isinstance(payload, dict) else None
            listed = payload.get("commits") if isinstance(payload, dict) else None
            if isinstance(size, int) and not isinstance(size, bool) and size >= 0:
                commits += size
                commits_known = True
            elif isinstance(listed, list):
                commits += len(listed)
                commits_known = True
    analyzed = sum(counts.values())
    return ActivitySection(
        status=AvailabilityStatus.AVAILABLE if analyzed else AvailabilityStatus.EMPTY,
        note=(
            "Based on GitHub's public events feed, which covers at most the last 90 days "
            "and 300 events; it is not a full contribution history."
        ),
        provenance=provenance,
        events_analyzed=analyzed,
        event_counts=dict(counts.most_common()),
        push_events=push_events,
        commits_reported=commits if commits_known else None,
        active_repositories=[name for name, _ in repos.most_common(20)],
        first_event_at=min(times) if times else None,
        last_event_at=max(times) if times else None,
        truncated=truncated,
    )


def normalize_search_item(raw: Any) -> ContributionItem | None:
    title = _str(raw, "title", 500)
    html_url = _github_html(_str(raw, "html_url", 500))
    if not (title and html_url):
        return None
    repo_api = _str(raw, "repository_url", 500) or ""
    prefix = "https://api.github.com/repos/"
    repository = repo_api[len(prefix) :] if repo_api.startswith(prefix) else None
    pull = raw.get("pull_request") if isinstance(raw, dict) else None
    merged = None
    if isinstance(pull, dict):
        merged = isinstance(pull.get("merged_at"), str)
    return ContributionItem(
        title=title,
        repository=repository,
        state=_str(raw, "state", 20),
        created_at=_dt(raw, "created_at"),
        closed_at=_dt(raw, "closed_at"),
        merged=merged,
        html_url=html_url,
    )
