"""Recorded outcome of the Phase 4 LeetCode feasibility investigation (2026-11-13).

Served by ``GET /api/v1/integrations/leetcode/status`` so the UI and API clients
can explain why no LeetCode data is collected automatically.
"""

from __future__ import annotations

INVESTIGATED_ON = "2026-11-13"
INTEGRATION_MODE = "manual_self_reported"

FINDINGS: tuple[str, ...] = (
    "LeetCode publishes no official, documented public API, developer program or "
    "OAuth integration for reading user profiles or submissions.",
    "LeetCode's Terms of Service (last modified 04/05/2024), under 'USE OF SERVICES - "
    "Restrictions', state that crawling, scraping or spidering any part of the Service, "
    "and reverse engineering it, are strictly forbidden.",
    "leetcode.com/robots.txt disallows /graphql, /api/ and /submissions for all user agents.",
    "Third-party libraries call LeetCode's internal, undocumented GraphQL endpoint "
    "(often with session cookies). It is not a supported API, may change without "
    "notice, and using it would conflict with the terms above.",
    "Conclusion: no permitted automated source exists. This system never contacts "
    "LeetCode; only manually entered, self-reported evidence is stored, labelled "
    "self-reported and not verified.",
)

SOURCES: tuple[str, ...] = (
    "https://leetcode.com/terms/",
    "https://leetcode.com/robots.txt",
)
