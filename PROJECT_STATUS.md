# Project Status

**Project:** AI-Powered Multi-Platform Candidate Intelligence and Recruitment Evaluation System
**Last updated:** 2026-11-13

| Phase | Status |
|---|---|
| Phase 0: Foundation | ✅ Complete and verified |
| Phase 1A: Resume Parsing Engine | 🟡 Implemented, not complete: the real Groq call has not run (no `GROQ_API_KEY`) |
| Phase 1B: Job Description Parsing Engine | 🟡 Implemented, not complete: same single blocker (no `GROQ_API_KEY`) |
| Phase 2: GitHub Developer Profile Integration | ✅ Complete and verified (real GitHub API) |
| Phase 3: Codeforces Developer Profile Integration | ✅ Complete and verified (real Codeforces API) |
| **Phase 4: LeetCode Feasibility and Safe Implementation** | ✅ **Complete and verified as a manual, self-reported evidence workflow.** No LeetCode API is used, because no official or permitted one exists |
| Phase 5+ (other platforms, aggregation, scoring, reports) | ⚪ Not started |

---

## LeetCode integration type

| Type | Applies? |
|---|---|
| Official API based | ❌ No: LeetCode publishes no official public API |
| Authorized integration based | ❌ No: no developer program, OAuth or partner integration exists |
| **Manual candidate-submitted evidence** | ✅ **Yes: the only mode implemented. Labelled self-reported and not verified** |
| Unavailable (automated collection) | ✅ Platform data is reported as `unavailable`, with the reason and sources |

**Feasibility findings (2026-11-13; primary sources read directly):**

1. No official, documented public API, developer program or OAuth integration exists for reading LeetCode profiles or submissions.
2. The [LeetCode Terms of Service](https://leetcode.com/terms/) ("Last modified: 04/05/2024", *USE OF SERVICES - Restrictions*) say that crawling, scraping or spidering any part of the Service, and reverse engineering it, are "strictly forbidden".
3. [leetcode.com/robots.txt](https://leetcode.com/robots.txt) disallows `/graphql`, `/api/` and `/submissions` for all user agents.
4. Third-party libraries call LeetCode's internal, undocumented GraphQL endpoint, often with session cookies. That is not a supported API, and using it would conflict with points 2 and 3.

**So this system never contacts LeetCode.** There is no sync and no retry, no cookies, and no passwords. It doesn't even check that a username exists.

---

## At a glance

| Area | Status |
|---|---|
| Backend (FastAPI, Python 3.12) | ✅ Health, capabilities, candidates, resumes, JDs, GitHub, Codeforces, **LeetCode (manual)** |
| Database (PostgreSQL 18, local) | ✅ `candidate_intel` at **`0005_self_reported_evidence` (head)**, applied 2026-11-13, no drift |
| Platform APIs | GitHub ✅ · Codeforces ✅ · LeetCode: none (by design) |
| LLM (Groq) | 🔴 Not configured (blocks 1A and 1B completion only) |
| Frontend (Next.js 16) | ✅ `/candidates` and the resume view now show GitHub, Codeforces and **LeetCode** sections |
| Automated tests | ✅ **1,284 passing**: backend 976 unit + 107 live, frontend 201. Real GitHub and Codeforces regression tests 4/4 (opt-in). **No real LeetCode test exists, because no LeetCode API is used** |
| Docker | ⚪ Not used (by decision) |

---

## Phase 4: verified (2026-11-13)

**End-to-end through the running backend (`ENABLED_PLATFORMS=github,codeforces,leetcode` for that process only) and your PostgreSQL:**

| Step | Result |
|---|---|
| `GET /integrations/leetcode/status` | `manual_self_reported`, `platform_api: unavailable`, `sync_supported: false`, `contacts_leetcode: false`, with both sources |
| Invalid inputs | `leetcode.cn` and `169.254.169.254` → `unsupported_domain`; problem URL and `/graphql` → `not_a_profile`; `bad!name` → `invalid_username` |
| Link `https://leetcode.com/u/jane_doe/` | 201; `sync_status: not_applicable`, `collection_status: unavailable`, ownership `unverified`; warnings say the username was not checked and ownership is not verified |
| `POST …/leetcode/sync` | 404 (does not exist) |
| Inconsistent evidence (easy + medium + hard ≠ total) | 422 with the specific reason; the submitted note is not echoed |
| Save evidence | 200: `self_reported` / `not_verified`, revision 1, provenance on every section. The contest section is "not provided" (null), **not zero** |
| Replace evidence | revision 2; omitted values are null; a real `contests_attended: 0` stays 0; reported by the recruiter |
| Clear evidence / remove | 204, association kept / 204 → 404 |
| Database row | `collected_data` and `collected_at` **null**; evidence only in `self_reported_data` (`not_verified`) |
| Backend log | **0 outbound HTTP requests**; the private note is not logged |
| Cleanup | E2E candidate deleted |

**Bug found by this run and fixed:** the "not in the future" date check used the UTC date. Between midnight and 05:30 IST it would have rejected today's date, so it now allows one day of leeway. A test covers this.

---

## Phase 4 scope delivered

| Requirement | Delivered |
|---|---|
| Profile association | Username, `https://leetcode.com/u/{username}/` or the legacy `https://leetcode.com/{username}/`. Rejects `leetcode.cn`, subdomains, problem, contest, discuss, `/graphql`, `/api` and other site pages, credentials, ports, encoded paths and reserved names. The username is **never sent anywhere**. Duplicates are detected case-insensitively; ownership stays unverified. |
| Evidence (manual) | Who reported it (candidate, or recruiter from the candidate's statement); "as of" date; total, easy, medium and hard solved; contest rating, contests attended and top %; contest history; languages and topics (optional counts); notes. Strict validation: integers only (no `true`/`"5"`), bounds, easy + medium + hard ≤ or = total, per-language/topic ≤ total, no contest details when attended = 0, history ≤ attended, no duplicates, no future dates or dates before 2015, no control characters, unknown keys rejected, at least one value. |
| Evidence quality | Every section has a status (`available` = provided, `not_collected` = not provided), a note, and provenance: platform `leetcode`, `source_type: self_reported`, `verification_status: not_verified`, `method: manual_entry`, reported_by, claimed profile URL, submitted_at. Revisions are counted. Freshness gives the age from "as of" (or submission) and flags it stale after 30 days (configurable). |
| Separation from platform evidence | Migration `0005` adds `self_reported_data` and `self_reported_at`. `collected_data` stays null for LeetCode. The schema accepts only `self_reported` / `not_verified`, and the frontend parser rejects anything claiming verification. |
| No contact with LeetCode | LeetCode modules import no HTTP client (checked by a test); no LeetCode endpoint, cookie or session string exists in the app (test); outbound requests fail the API and live tests (patched guard); the E2E log shows none. |
| API | Link, get, put evidence, clear evidence, remove, integration status (findings and sources); capabilities report the mode. No sync or retry endpoints exist, because no permitted source does. |
| Frontend | LeetCode section: an explanation with the findings and source links; link; "Platform data: not available"; an evidence form (empty means not provided); a view where everything is badged "Self-reported · not verified"; stale warning; edit, clear, remove. The API client now shows the server's validation details. |

---

## Test results (2026-11-13)

| Suite | Result |
|---|---|
| Backend lint / format / types | ✅ ruff clean (129 files); mypy strict: 0 issues in 128 files |
| Backend unit | ✅ **976 passed** (135 new) |
| Backend live (PostgreSQL) | ✅ **107 passed**, 6 skipped (opt-in real GitHub, Codeforces and Groq) |
| Backend combined coverage | ✅ **97%** (LeetCode modules 98-100%) |
| Real GitHub and Codeforces regression (opt-in) | ✅ **4/4 passed** |
| Real LeetCode API | ⚪ **Not applicable**: no LeetCode API is used |
| Frontend lint / typecheck / build | ✅ |
| Frontend tests | ✅ **201 passed** (16 files; 29 new) |

Details: [TEST_REPORT.md](TEST_REPORT.md).

---

## Open items

| # | Item | Impact | Action |
|---|---|---|---|
| 1 | **`ENABLED_PLATFORMS` is empty in your `backend/.env`** | Platform features are disabled in your normal runs | Set `ENABLED_PLATFORMS=github,codeforces,leetcode` |
| 2 | **`frontend/src/app/resumes/page.tsx` is missing from the pushed commit** (`.gitignore` fixed in Phase 3) | A fresh clone has no `/resumes` page | Include it and `frontend/src/app/candidates/page.tsx` in the next commit |
| 3 | `GROQ_API_KEY` not set | Phases 1A and 1B cannot be closed | See DEVELOPMENT_LOG.md |
| 4 | LeetCode evidence cannot be verified | It is a claim, not proof | Shown everywhere as self-reported; later phases must weight it accordingly (e.g. ask for a screen-share in interview) |
| 5 | LeetCode position should be re-checked if its terms change | An official API could appear | Re-run the feasibility check before any change |
| 6 | Ownership verification not implemented (all platforms) | Profiles stay `unverified` | Later phase |
| 7 | In-process background tasks; no authentication | As in earlier phases | Later phases |
| 8 | Phase 2 synthetic E2E candidate remains | One test row | Later cleanup |
| 9 | Phases 2-4 not committed yet | | Commit and push when you are ready (as NiranjanKJ304) |

---

## How to run

```powershell
# backend/.env:  ENABLED_PLATFORMS=github,codeforces,leetcode
cd backend
uv run alembic upgrade head                 # 0005_self_reported_evidence
uv run uvicorn app.main:create_app --factory --reload --port 8000

cd frontend
npm run dev                                  # http://localhost:3000/candidates
```

---

[README.md](README.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [API_DOCUMENTATION.md](API_DOCUMENTATION.md) ·
[DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md) · [TEST_REPORT.md](TEST_REPORT.md)
