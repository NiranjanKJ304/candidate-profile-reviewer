# Test Report

Every result below was captured from real runs. Nothing is assumed. A check that could
not run is marked **NOT RUN** with the reason.

---

## Maintenance: HTTP 413 fix (2026-11-14)

| Check | Result |
|---|---|
| Reproduce: the user's failed evaluation, prompt rebuilt locally | 23 requirements, 61 evidence items, 23,489 characters (~6,500 tokens) |
| Groq response for that request | 413 "TPM: Limit 8000, Requested 9563" (refused before processing) |
| **Same evaluation with the budget, real Groq (not saved)** | ✅ fits; 24 of 61 items sent; answered in 7.1 s; score 50, Moderate |
| New unit tests `test_evaluation_budget.py` | ✅ 10: untouched when small; low-priority dropped first; essentials kept; text shortening; impossible budget → no request; only the best 3 references per requirement are essential; omitted evidence cannot back a verdict; corrective retry does not resend the previous reply; 413 message has numbers but no account id or key |
| New live test | ✅ trimmed evidence stored with `sent_to_llm: false`; cited ids only from sent items; warning recorded |
| Backend combined | ✅ **1,206 passed**, 7 skipped (opt-in), 96%; ruff and mypy clean |
| Frontend | ✅ 215 passed ("not sent to the model" label); lint, typecheck and build clean |

---

## Phase 5: Candidate–JD Evaluation (2026-11-14)

### Summary

| Check | Command | Result |
|---|---|---|
| Backend format / lint | `uv run ruff format --check .` / `uv run ruff check .` | ✅ 145 files / all checks passed |
| Backend types | `uv run mypy` (strict) | ✅ 0 issues in 144 source files |
| Backend unit | `uv run pytest -q` | ✅ **1,073 passed** |
| Backend combined (unit + live PostgreSQL) | `INTEGRATION_DATABASE_URL=… uv run pytest -m "" --cov` | ✅ **1,195 passed, 7 skipped** (opt-in real GitHub ×2, Codeforces ×2, Groq ×3), **96%** |
| **Real Groq** | `INTEGRATION_GROQ_API_KEY=… uv run pytest -m integration tests/integration/test_llm_provider_live.py` | ✅ resume, JD and **evaluation**: 3/3 (evaluation re-run after the prompt change to `eval-v2`: passed) |
| Migration on your DB | `uv run alembic upgrade head` / `current` / `check` | ✅ `0005 → 0006_evaluation_errors (head)`; no drift |
| Frontend lint / typecheck | `npm run lint` / `npm run typecheck` | ✅ |
| Frontend tests | `npm test` | ✅ **215 passed** (18 files) |
| Frontend build | `npm run build` | ✅ `/`, `/candidates`, `/evaluations`, `/job-descriptions`, `/resumes` |
| Earlier phases | (included above) | ✅ no regressions |

### Coverage of the evaluation modules (combined run)

| Module | Coverage |
|---|---|
| `api/v1/evaluations.py`, `schemas/evaluation.py`, `redact.py`, `scoring.py` | 100% |
| `assessment.py` | 99% |
| `experience.py` | 95% |
| `processor.py` | 93% |
| `evidence.py`, `matching.py`, `skills.py` | 91% |

### New tests

| File | Tests | Covers |
|---|---|---|
| `test_evaluation_building.py` | 50 | redaction (name parts, usernames, e-mail, phone, URLs; dates and numbers kept; short names not over-redacted); skill keys, platform language keys, whole-word mentions, tags; month parsing and **overlap-merged experience**, unknown → null; **bundle trust levels per source, unique ids, no identity or bias-prone fields (institution, grade, location, followers, company, usernames)**, resume-only, empty, forks excluded with source URLs kept; requirements from the JD (alias duplicate dropped, soft skills not scored, mandatory education), **duplicate qualifications not counted twice**, cap; baseline by trust (platform / resume / self-reported / none); **scoring** (weights, cap at Moderate, **null below 50% coverage**, unevidenced and unmet lists, weak, empty) |
| `test_evaluation_assessment.py` | 19 | **prompt contains no identity, links, institution or grades**; evidence sent without URLs; system-prompt rules (fairness, untrusted data, summary consistency); delimiter spoofing neutralised; **guards** (valid kept; met without valid evidence → insufficient; not met without evidence → insufficient; not met despite platform evidence → review warning; self-reported only ×0.75; duplicates, unknown and missing verdicts; strengths/gaps cleaned); **corrective retry** (accepted when better; ignored when worse; first reply kept when the retry is invalid); invalid schema → `llm_invalid_response`; provider errors mapped (auth, timeout, rate limit); not configured → no call |
| `test_evaluation_api.py` | 26 | create (defaults to latest parsed resume, none, explicit); bad resume (missing, other candidate, unparsed); JD not parsed (processing, empty, unreadable); unknown candidate/JD; LLM not configured; in progress (and stale run not blocking); schema; unreadable stored result; list; rerun (reset error, resolves missing resume, busy, unconfigured); delete and 404s; DB down on 5 endpoints; reviewed JD precedence and fallback |
| `test_config.py` / `test_llm_client.py` | (existing, updated) | new default model |
| `test_migrations_offline.py` | +1 | 0006 SQL up/down; 6-revision chain |
| `test_evaluation_workflow_live.py` | 15 | PostgreSQL: **full evaluation** (resume + GitHub; Python met via platform evidence; Rust insufficient, not "not met"; score persisted as numeric); **the LLM never receives identity**; reviewed JD used; platform-only; no evidence → `no_evidence` without an LLM call; JD without requirements; **LLM timeout recorded, re-run succeeds**; unparsed JD/resume rejected; list filters; **JD delete blocked (409), candidate delete cascades**; error-code CHECK constraint; logs free of identity; **all four sources with trust levels (Rust met only via LeetCode → credit ×0.75; Codeforces and LeetCode usernames not sent)**; linked-but-not-collected sources and unusable resume; unexpected error → `internal_error`, not stuck |
| `test_migrations_live.py` | (updated) | head 0006; 0005 round trip still works through 0006 |
| `test_llm_provider_live.py` | +1 | **real Groq evaluation**: every requirement has a verdict; Python met or partial; Rust not met-as-met; cited ids valid |
| `frontend/src/lib/evaluations.test.ts` | 9 | parsers (completed; null score stays null; pending/failed; rejects unknown verdict, trust, string score, status), page, create call body |
| `frontend/.../EvaluationWorkspace.test.tsx` | 5 | evaluate → poll → score, label, notice, **unproven required list**, verdict table, adjusted note, evidence links, summary disclaimer, sources note; only parsed JDs offered; no-number state; failure explanation and re-run from history; selection validation and server error; config error |

### Real Groq checks

**Synthetic bundle** (resume + GitHub, model `openai/gpt-oss-120b`): Python met (cites G3, G7), PostgreSQL met, **Rust insufficient evidence**, Kubernetes partially met (resume claim only), experience met (7.4-year estimate), education met, REST APIs met. Score 80.8, Strong, coverage 0.846.

**Real end-to-end** through the running backend:

| Step | Result |
|---|---|
| Resume PDF → real Groq parse | completed, 3.1 s |
| JD → real Groq parse | completed, 4.6 s |
| GitHub `octocat` (real) | collected |
| Evaluation | **completed, 6.1 s**, score 95.7, Strong; Kubernetes insufficient; `overall_score` a number |
| Identity in evidence sent to the LLM | none (0 e-mails) |
| Backend log | 67 lines, 0 e-mails, no name, 0 errors |
| JD delete while evaluated | 409 |
| Cleanup | all records and files removed |

One run hit Groq's free-tier per-minute token limit (HTTP 429). It was correctly reported as `llm_rate_limited`, and the call succeeded about a minute later.

### Coverage gaps (honest)

- `evidence.py`: some optional-field branches (e.g. GitHub activity or PR totals missing, Codeforces unrated with no rated contests).
- `processor.py`: "selected resume belongs to another candidate" (prevented by the API) and the SQLAlchemy re-raise.

---

## Maintenance: Groq model fix (2026-11-14)

| Check | Result |
|---|---|
| `GET {groq}/models` with the configured key | 200; `llama-3.3-70b-versatile` **not listed** |
| Chat completion with `llama-3.3-70b-versatile` | 404 `model_not_found` (reproduces the reported error) |
| JSON-mode extraction with `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b` | 200, valid JSON for all three |
| **Real-provider tests** `INTEGRATION_GROQ_API_KEY=… uv run pytest -m integration tests/integration/test_llm_provider_live.py` | ✅ **2 passed** (`test_real_resume_structuring`, `test_real_jd_structuring`), real Groq, `openai/gpt-oss-120b` |
| Backend unit (incl. new 404-message test) | ✅ 977 passed |
| ruff / mypy strict | ✅ clean / 0 issues |

---

## Phase 4: LeetCode Feasibility and Safe Implementation (2026-11-13)

**Integration type tested: manual, self-reported evidence.** No LeetCode API is used, so **no real LeetCode API test exists or is claimed.** The "real" checks below exercise the running backend and your PostgreSQL only.

### Summary

| Check | Command | Result |
|---|---|---|
| Backend format / lint | `uv run ruff format --check .` / `uv run ruff check .` | ✅ 129 files / all checks passed |
| Backend types | `uv run mypy` (strict) | ✅ 0 issues in 128 source files |
| Backend unit | `uv run pytest -q` | ✅ **976 passed** |
| Backend live (PostgreSQL) | `INTEGRATION_DATABASE_URL=… uv run pytest -m integration -rs` | ✅ **107 passed, 6 skipped** (opt-in real GitHub ×2, Codeforces ×2, Groq ×2) |
| Backend combined | `uv run pytest -m "" --cov` | ✅ 97% coverage |
| Real GitHub + Codeforces regression | `INTEGRATION_GITHUB_LIVE=1 INTEGRATION_CODEFORCES_LIVE=1 uv run pytest -m integration tests/integration/test_github_real_api.py tests/integration/test_codeforces_real_api.py` | ✅ **4 passed** (real network) |
| Real LeetCode API | none | ⚪ **Not applicable**: no LeetCode API is used |
| Migration on your DB | `uv run alembic upgrade head` / `current` / `check` | ✅ `0004 → 0005_self_reported_evidence (head)`; no drift |
| Frontend lint / typecheck | `npm run lint` / `npm run typecheck` | ✅ |
| Frontend tests | `npm test` | ✅ **201 passed** (16 files) |
| Frontend build | `npm run build` | ✅ all 5 routes |
| Resume / JD / GitHub / Codeforces regressions | (included above) | ✅ none |

The combined run (unit + live) recorded 1,082 passed before the timezone fix. The fix only touches input validation, and it added one unit test; the unit suite was re-run afterwards (976 passed). The pre-existing Starlette `testclient` deprecation warning from two Phase 0 tests is unchanged.

### Coverage of the LeetCode modules

| Module | Coverage |
|---|---|
| `services/leetcode/{validation,evidence,feasibility}.py` | 100% |
| `schemas/leetcode.py` | 99% |
| `api/v1/leetcode.py` | 98% (IntegrityError race on concurrent links not exercised) |

### New tests

| File | Tests | Covers |
|---|---|---|
| `test_leetcode_validation.py` | 50 | 13 valid forms (`/u/`, legacy, `@`, www/http, case, query and fragment, full-width homoglyphs); 35 rejections incl. SSRF (`169.254.169.254`, `localhost`, `leetcode.com@host`, look-alike domains), `leetcode.cn`, subdomains, ports, credentials, schemes, problem/contest/discuss/`/graphql`/`/api`/`/submissions` URLs, reserved names, encoded paths, NUL, length; no echo of input; canonical URL |
| `test_leetcode_evidence.py` | 49 | full payload; 26 rejections (empty, sum > total, sum ≠ total, language/topic > total, contest details with 0 attended, history > attended, duplicates, bounds, date before 2015, blank or control-character names and notes, list limits, extra keys, **`true` and `"1500"` as numbers**); future dates; timezone leeway; `reported_by` required and limited; normalisation; **zero is a real value**; **provenance on every section** (`self_reported`, `not_verified`, `manual_entry`, reporter, claimed URL, time); **not provided is null and `not_collected`, never zero**; recruiter entry and revisions; strict stored schema refuses `verified` / `platform_api`; age; **structural guarantees: LeetCode modules import no HTTP client; no LeetCode endpoint, cookie or session string anywhere in `app/`**; findings cite the primary sources |
| `test_leetcode_api.py` | 32 | **any outbound HTTP fails the test (guard)**; link → 201 with `unavailable` / `not_applicable` / `unverified`, warnings and `collected_data` null; 5 invalid inputs; schema; duplicates; unknown candidate; evidence saved with labels while `collected_*` stay null; revision increments; 5 invalid evidence payloads (values not echoed, nothing stored); stale and fresh; unreadable stored evidence; clear keeps the association; remove; **no `/sync` or `/retry`**; disabled (503, reads still work); 404s; DB down on 5 endpoints; status (findings, sources, `contacts_leetcode: false`); capabilities |
| `test_config.py` (LeetCode) | 3 | enablement; stale-days bounds |
| `test_migrations_offline.py` | +1 (updated chain) | 0005 upgrade and downgrade SQL |
| `test_leetcode_workflow_live.py` | 7 | PostgreSQL with an outbound guard: link persists with `collected_data`/`collected_at` null; submit, replace (no merge, unknowns null) and clear; invalid evidence not persisted (note not echoed); duplicate and re-association; candidate cascade; **GitHub, Codeforces and LeetCode on one candidate, and the GitHub and Codeforces sync services leave the LeetCode row untouched**; logs free of evidence content |
| `test_migrations_live.py` | +1 (version 0005) | **0005 down/up on a populated table**: `not_applicable` → `pending`, columns dropped, re-upgrade, CHECK constraint enforced |
| `frontend/src/lib/leetcode.test.ts` | 22 | parsers reject evidence or sections claiming verification, a platform source or available platform data; form → payload (empty omitted, real 0 kept, lists, contest history); 8 client rejections; edit-form round trip; server `details` surfaced; raw profile posted |
| `frontend/src/components/leetcode/LeetCodeSection.test.tsx` | 7 | explanation with findings and source links and no sync button; link → evidence form → saved view badged "Self-reported · not verified", "not provided (unknown, not zero)", exact payload; client validation; server validation details; edit pre-fill and stale warning; clear and remove; disabled |

The CandidatesWorkspace test now also checks that the LeetCode section opens.

### End-to-end through the running backend (manual workflow; no LeetCode contact)

| Action | Result |
|---|---|
| Status / capabilities | `manual_self_reported`, `platform_api: unavailable`, `sync_supported: false`, `contacts_leetcode: false`, both sources |
| 5 invalid inputs | `unsupported_domain` ×2 (`leetcode.cn`, `169.254.169.254`), `not_a_profile` ×2 (problem URL, `/graphql`), `invalid_username` |
| Link | 201, `not_applicable` / `unavailable` / `unverified`, `evidence: null`, 2 warnings |
| `POST …/sync` | 404 |
| Inconsistent evidence | 422 "easy + medium + hard solved must equal total solved"; note not echoed |
| Save evidence | 200, `self_reported` / `not_verified`, rev 1, total 300, contest `not_collected` / `null`, full provenance |
| Freshness | as_of 2026-11-01, 11 days, not stale (threshold 30) |
| Replace | rev 2, total `null` (not provided), attended `0` (real zero), recruiter |
| Clear / remove | 204 → evidence null / 204 → 404 |
| DB row | `not_applicable`, `unavailable`, `collected_data` and `collected_at` null, `self_reported_data` `not_verified` |
| Backend log (31 lines, DEBUG) | **0 outbound HTTP lines**, no LeetCode request, private note absent |
| Cleanup | candidate deleted |

### Coverage gaps (honest)

- The IntegrityError race on two simultaneous links for one candidate is not exercised (the unique constraint still enforces it).

---

## Phase 3: Codeforces Developer Profile Integration (2026-11-12)

**Environment:** your PostgreSQL 18 (live tests in random temporary schemas), Memurai 8.2, Python 3.12.16, Node 24. Codeforces API reachable (public methods, no key).

### Summary

| Check | Command | Result |
|---|---|---|
| Backend format / lint | `uv run ruff format --check .` / `uv run ruff check .` | ✅ 119 files / all checks passed |
| Backend types | `uv run mypy` (strict) | ✅ 0 issues in 118 source files |
| Backend unit | `uv run pytest -q` | ✅ **841 passed** |
| Backend live (PostgreSQL) | `INTEGRATION_DATABASE_URL=… uv run pytest -m integration -rs` | ✅ **99 passed, 6 skipped** (2 real-Codeforces opt-in, 2 real-GitHub opt-in, 2 Groq) |
| Backend combined | `uv run pytest -m "" --cov` | ✅ **940 passed, 6 skipped, 96%** |
| **Real Codeforces API** | `INTEGRATION_CODEFORCES_LIVE=1 uv run pytest -m integration tests/integration/test_codeforces_real_api.py -v` | ✅ **2 passed** (real network, paced) |
| Migration | `uv run alembic current` / `check` | ✅ `0004_developer_profile_sync (head)`, no new operations (no Phase 3 migration) |
| Frontend lint / typecheck | `npm run lint` / `npm run typecheck` | ✅ |
| Frontend tests | `npm test` | ✅ **172 passed** (14 files) |
| Frontend build | `npm run build` | ✅ `/`, `/_not-found`, `/candidates`, `/job-descriptions`, `/resumes` |
| GitHub / resume / JD regressions | (included above) | ✅ none |

One pre-existing warning appears in the run: a Starlette deprecation emitted when two Phase 0 tests import `fastapi.testclient`. It is unrelated to this phase.

### Coverage of the Codeforces modules (combined run)

| Module | Coverage |
|---|---|
| `schemas/codeforces.py`, `services/codeforces/{analytics,collector,normalize,validation}.py` | 100% |
| `services/codeforces/sync.py` | 99% |
| `api/v1/codeforces.py` | 98% (IntegrityError race on concurrent adds not exercised) |
| `integrations/codeforces.py` | 98% (network-error retry-then-success branch not exercised) |

### New tests

| File | Tests | Covers |
|---|---|---|
| `test_codeforces_validation.py` | 58 | 16 valid forms (URL variants, `-is-this-fft-`, full-width homoglyphs); 40 rejections including SSRF (`169.254.169.254`, `localhost`, `[::1]`, `codeforces.com@host`, look-alike and sub-domains, `codeforces.ru`, ports, credentials, `file:`/`javascript:`/`ftp:`), contest, problem, blog and submission URLs, encoded paths, `;`, NUL/DEL, length; no echo of input |
| `test_codeforces_client.py` | 34 | fixed URL and headers (no auth); method allow-list; parameter encoding; FAILED not found / other / no comment; malformed bodies (HTML, no status, list, no result, empty, 403 text); call limit retried with backoff ≥ 2 s and exhausted; 429; 5xx retry then success and exhaustion; FAILED 5xx not retried; timeout and network errors; redirects (same API followed once; foreign, http, other port, non-API path, relative and second redirect refused); **pacing** (2 s between calls, no wait when already elapsed, concurrent calls serialised) |
| `test_codeforces_collector.py` | 63 | user normalisation (drops e-mail, VK/OpenID, avatar; unrated; bad timestamps); rating changes (delta, gym URL, malformed); submissions (keys, URLs, practice relative time, judging, gym/problemset/name-only keys, unsafe index, tag cleaning, **no team-mate handles**, malformed); **analytics** (submissions vs unique problems, accepted vs unique accepted, rejected and pending, difficulty and tag counted once per problem, verdicts, languages, months, contests, empty, order-independent); collection (3 calls, provenance on every section, no private data, canonical handle, unrated warning, empty account, malformed counted); **limits and paging** (truncation detection with limit + 1, exact limit, multi-page, short page, hard max 1000, duplicates across pages, API ignoring `from`); **partial failures** (rating error → `rated_contests` null; submissions error → statistics null, not zero; rate limit skips later calls; later page failure keeps items; unexpected payload); profile not found, incomplete user, rate limit |
| `test_codeforces_api.py` | 35 | URL input → 202 and scheduled; 6 invalid inputs → 422 with **zero network calls**; schema; disabled (503, stored data still readable); unknown candidate; duplicates; analytics and freshness in GET; stale snapshot; unreadable snapshot; sync, 409 while running, stale running sync restartable; **retry only after failure** (409 `not_failed`, 202 keeps the previous data); submissions paging and filters; rating history; listings before collection; delete; DB down on 7 endpoints → 503 without driver details; 404s; status without probing; status reports last error; capabilities |
| `test_codeforces_sync.py` | 12 | success (canonical handle, external id); handle change warning; other-candidate warning; disabled → no network; unknown user → unavailable; failure without data → failed; **failed retry preserves the previous snapshot** (rate limit, API error); unexpected error keeps the snapshot; DB lost while recording; crash logged not raised; missing or other-platform rows ignored |
| `test_config.py` (Codeforces) | 6 | defaults; enablement; interval < 2 s, limit > 1000 or 0, timeout 0 and retries 9 rejected |
| `test_codeforces_workflow_live.py` | 14 | PostgreSQL: add → sync → retrieve (row fields, provenance, analytics); **e-mail and team-mate handles absent from responses and stored JSONB**; listings; case-insensitive duplicate; unknown user → unavailable; rate-limited then `/retry` succeeds (then 409); **failed refresh preserves the stored snapshot, `collected_at` and API output**; partial sync keeps unknowns null; truncated window flagged; 409 while in progress; same account on two candidates; **GitHub and Codeforces coexist** (deleting one leaves the other); delete, re-add and candidate cascade; logs free of private data |
| `test_codeforces_real_api.py` | 2 | **Real Codeforces:** `tourist` collected (limit 20, sections valid, provenance, statistics consistent, no e-mail pattern, no `members`/`source` keys, ≤ 4 requests); unknown handle → not found |
| `frontend/src/lib/codeforces.test.ts` | 16 | parsers (collected, unavailable statistics stay null, not collected, 5 rejections, status), client input checks, calls (raw value posted, validation code surfaced, `/retry` vs `/sync`, DELETE 204) |
| `frontend/src/components/codeforces/CodeforcesSection.test.tsx` | 8 | connect → poll → rating, rank, organisation, "not a complete measure" notice, window text and truncation notice, unique vs attempted, difficulty (incl. Unrated), topics, languages, contest link and negative delta, problem link, monthly activity, warning, pacing note; client and server validation; failed sync keeps data and retries via `/retry`; unavailable submissions explained (no zero tiles); stale data; remove; disabled |

The existing CandidatesWorkspace test now also checks that the Codeforces section opens.

### Real Codeforces API: end-to-end through the running backend

`ENABLED_PLATFORMS=github,codeforces` (process variable only), your PostgreSQL:

| Action | Result |
|---|---|
| `GET /integrations/codeforces/status` | enabled, 3 methods, 2 s interval, limit 500, 0 requests made |
| 5 invalid inputs | `unsupported_domain` ×2 (incl. `http://169.254.169.254/latest/meta-data`), `not_a_profile`, `invalid_handle` ×2; still **0 requests** |
| `POST …/codeforces {"profile":"https://codeforces.com/profile/tourist"}` | 202 → `completed` / `collected` in 5.2 s, **3 API requests**, ownership `unverified`, not stale |
| Profile / lifetime | rating 3408 / 4009, legendary grandmaster / tourist, registered 2010-02-12, **311 rated contests** |
| Window | 500 submissions (2025-01-26 to 2026-11-07, truncated plus warning), 357 accepted, **342 unique accepted**, 143 rejected; top tags greedy, math, dp |
| Sections | profile, rating_history, submissions, analytics: available, each with API method and source URL |
| `GET …/submissions?verdict=OK&limit=5` | total 357, 5 returned |
| `GET …/rating-history?limit=3` | total 311, first "Codeforces Beta Round 2" |
| Duplicate `TOURIST` / `/retry` while completed | 409 `duplicate_profile` / 409 `not_failed` |
| `POST …/sync` | 202 → `completed`, `collected_at` updated |
| Unknown handle (second candidate) | `failed` / `invalid_profile` / `unavailable`, `data: null` |
| Status after the run | `requests_made` 7 (3 + 3 + 1), `last_error` "Codeforces user not found" |
| Privacy | 0 e-mail addresses in the API response and in the 60-line backend log; no `members`; no submission or problem text in logs |
| `DELETE …/codeforces` | 204 → GET 404 |
| Cleanup | both synthetic candidates deleted (cascade) |
| Frontend production server `/candidates` | 200, Codeforces section present |

Preserving the previous snapshot after a failed sync could not be forced on the real API. It is verified by the unit tests and by the live PostgreSQL test listed above.

### Coverage gaps (honest)

- **Client:** a network error followed by a successful retry is not exercised; timeout-then-success is.
- **API:** the IntegrityError race on concurrent adds is not exercised.
- **Sync:** the SQLAlchemy re-raise inside the collection step is not exercised.

---

## Phase 2: GitHub Developer Profile Integration (2026-11-12)

**Environment:** your PostgreSQL 18 (live tests in random temporary schemas), Memurai 8.2, Python 3.12.16, Node 24. GitHub API reachable; **no `GITHUB_TOKEN`** (unauthenticated: 60 core requests/hour, 10 searches/minute).

### Summary

| Check | Command | Result |
|---|---|---|
| Backend format / lint | `uv run ruff format --check .` / `uv run ruff check .` | ✅ 102 files / all checks passed |
| Backend types | `uv run mypy` (strict) | ✅ 0 issues in 101 source files |
| Backend unit | `uv run pytest -q` | ✅ **633 passed** |
| Backend live (PostgreSQL) | `INTEGRATION_DATABASE_URL=… uv run pytest -m integration -rs` | ✅ **85 passed, 4 skipped** (2 real-GitHub opt-in, 2 Groq) |
| Backend combined | `uv run pytest -m "" --cov` | ✅ **718 passed, 4 skipped, 96%** |
| **Real GitHub API** | `INTEGRATION_GITHUB_LIVE=1 uv run pytest -m integration tests/integration/test_github_real_api.py -v` | ✅ **2 passed** (real network, unauthenticated) |
| Migration on your DB | `uv run alembic upgrade head` / `current` / `check` | ✅ `0003 → 0004_developer_profile_sync (head)`; no drift |
| Frontend lint / typecheck | `npm run lint` / `npm run typecheck` | ✅ |
| Frontend tests | `npm test` | ✅ **148 passed** (12 files) |
| Frontend build | `npm run build` | ✅ `/`, `/_not-found`, `/candidates`, `/job-descriptions`, `/resumes` |
| Phase 1A/1B regressions | (included above) | ✅ none |

### Coverage of the GitHub modules (combined run)

| Module | Coverage |
|---|---|
| `schemas/github.py` | 100% |
| `services/github/collector.py` | 98% |
| `services/github/validation.py` | 98% |
| `api/v1/github.py` | 95% |
| `services/github/sync.py` | 95% |
| `integrations/github.py` | 94% |
| `services/github/normalize.py` | 94% |

### New tests

| File | Tests | Covers |
|---|---|---|
| `test_github_validation.py` | 52 | 15 valid forms; 34 rejections incl. SSRF (`169.254.169.254`, `localhost`, `[::1]`, `github.com@host`, look-alike and sub-domains, ports, credentials, `file:`/`javascript:`/`ftp:`), repository/sub-page URLs, reserved names, encoded slashes, traversal, NUL, length; full-width homoglyph normalisation; no echo of input |
| `test_github_client.py` | 34 | headers with and without token; unsafe paths; pagination (multi-page, cap and `truncated`, foreign link ignored, non-list payload); redirects (same host followed once; foreign, http, other port and relative refused); 404/451/401/422/403 mapping; primary and 429 rate limits (no waiting); message-only limit; short secondary limit retried; long not; 5xx retry then success and exhaustion; timeout; network error; malformed JSON; **token never in errors or logs**; `/rate_limit` parsing |
| `test_github_collector.py` | 34 | profile normalisation drops e-mail; missing and malformed fields; unusable or private repositories skipped; README decoding, size cap, excerpt cap, empty, wrong format, binary; events keep counts only (**no commit messages or e-mails**); unknown commit counts stay null; search items; aggregation; full collection; provenance on every section; empty repositories are not missing skills; malformed counted; missing README is `empty`; README limit; **rate limit mid-collection gives a partial snapshot without further calls**; isolated repository failure; incomplete and 422 search; validated login in queries; profile 404, organisation, rate limit and incomplete payload; token mode; configuration switches |
| `test_github_api.py` | 21 | URL input → 202 and scheduled; 6 invalid inputs → 422 with **zero network calls**; schema validation; disabled integration (503, status not probed); unknown candidate; duplicates (same name case-insensitive, or another profile); DB down on 5 endpoints → 503 without driver details; 404s; status with token (value never shown) and unreachable; capabilities |
| `test_github_sync.py` | 7 | success; disabled → no network; organisation → unavailable; external-id change warning; unexpected error recorded; DB lost while recording a failure; non-GitHub or missing rows ignored |
| `test_github_workflow_live.py` | 15 | PostgreSQL: add → sync → retrieve (row fields); **e-mail and commit text absent from the API response and the stored JSONB**; repository filters and paging; listing before collection; case-insensitive duplicate; unknown user → `unavailable`; rate-limited then retry succeeds; **failed retry keeps the previous snapshot**; partial sync warnings; 409 while in progress; canonical login; same account on two candidates; delete, re-add and candidate cascade; candidate list; logs free of private data |
| `test_github_real_api.py` | 2 | **Real GitHub:** `octocat` collected (sections valid, provenance, no e-mail pattern, ≤ 8 requests); unknown user → not found |
| `frontend/src/lib/github.test.ts` | 12 | parsers (collected, not collected, 4 rejections, status, candidate page), client input checks, calls (raw value posted, validation code surfaced, DELETE 204) |
| `frontend/src/components/github/GitHubSection.test.tsx` | 9 | connect → poll → summary, repository links, fork badge, languages, empty topics note, rate-limited badge, "not coding ability" notice, token warning, quota; client validation; server validation message; failed sync keeps data; retry; remove; disabled; candidates workspace create/select; config error |

### Real GitHub API: end-to-end through the running backend

`ENABLED_PLATFORMS=github` (process variable only), unauthenticated:

| Action | Result |
|---|---|
| `GET /integrations/github/status` | enabled, reachable, core 60/60, search 10/10 |
| 4 invalid inputs | `unsupported_domain` ×2 (incl. `http://169.254.169.254/latest`), `not_a_profile`, `invalid_username` |
| `POST …/github {"profile":"https://github.com/octocat"}` | 202 → `completed` / `collected`, 11 API requests, ownership `unverified` |
| Snapshot | profile, repositories (8), languages, PRs (8), issues (5): available · topics, activity: empty · calendar: unavailable · all with source URLs |
| `GET …/repositories?include_forks=false` | 6 |
| Duplicate `OCTOCAT` | 409 `duplicate_profile` |
| `POST …/sync` | 202 → `completed`, new `collected_at` |
| Log and DB scans | 0 e-mail addresses; no `Authorization` in logs |
| `DELETE …/github` | 204 → GET 404 |
| Frontend `/candidates` | 200 |

### Coverage gaps (honest)

- **Client:** non-numeric `x-ratelimit` headers, a timeout on the very last retry boundary, and a malformed `/rate_limit` entry are not exercised.
- **API:** the unreadable-stored-snapshot path and the IntegrityError race on concurrent adds are not exercised.
- **Sync:** the "sync crashed" outer handler path is not exercised.

---

## Phase 1B: Job Description Parsing Engine (2026-11-12)

**Environment:** your PostgreSQL 18 (live tests in random temporary schemas), Memurai 8.2, Tesseract 5.4.0, Python 3.12.16, Node 24. **No `GROQ_API_KEY`.**

### Summary

| Check | Command | Result |
|---|---|---|
| Backend format / lint | `uv run ruff format --check .` / `uv run ruff check .` | ✅ 86 files formatted / all checks passed |
| Backend types | `uv run mypy` (strict) | ✅ 0 issues in 85 source files |
| Backend unit | `uv run pytest -q` | ✅ **484 passed** |
| Backend live | `INTEGRATION_DATABASE_URL=… INTEGRATION_REDIS_URL=… uv run pytest -m integration -rs` | ✅ **70 passed, 2 skipped** (`INTEGRATION_GROQ_API_KEY not set`) |
| Backend combined | `uv run pytest -m "" --cov` | ✅ **554 passed, 2 skipped, 95% coverage** |
| Migration on your DB | `uv run alembic upgrade head` / `current` / `check` | ✅ `0002 → 0003_job_description_parsing (head)`; no drift |
| Frontend lint / typecheck | `npm run lint` / `npm run typecheck` | ✅ 0 problems / 0 errors |
| Frontend tests | `npm test` | ✅ **124 passed** (10 files) |
| Frontend build | `npm run build` | ✅ `/`, `/_not-found`, `/job-descriptions`, `/resumes` prerendered |
| **Real Groq structuring** | `test_llm_provider_live.py` | ⛔ **NOT RUN**: no key (2 tests skipped) |

### Requested categories → evidence

| Category | Where |
|---|---|
| Valid PDF, DOCX, TXT | `test_jd_documents.py` (6 valid variants incl. UTF-16 and BOM), `test_jd_api.py` (202 for each), `test_jd_workflow_live.py` (3 formats upload → DB → parsed) |
| Scanned PDF | real-OCR unit test; live scanned upload → `pdf_ocr` → LLM input contains the OCR text |
| Corrupted and empty | empty, whitespace-only TXT, truncated PDF, corrupted bytes, undecodable cp1252 bytes, too-short TXT → `no_text` |
| Missing requirements | minimal JSON → empty lists and nulls (unit + live); `JDView` shows "No … stated" for 7 sections |
| Invalid LLM responses | 9 schema violations (unknown key, wrong type, missing name, bad enum, bad seniority, inverted years, negative years, bad education level, non-object), retry-then-success, live non-JSON and wrong type stored as `llm_invalid_response` |
| LLM timeout / API failure | unit `llm_timeout`, `llm_rate_limited`, `llm_error`, `llm_not_configured`; live: all stored on the row with text kept and **title left null** |
| Database failures | DB down on duplicate check → 503 (no driver details); commit failure → 503 and no orphan file; 4 read/delete endpoints with DB down → 503; processor: DB down at start / while recording failure → logged, never crashes |
| Duplicate uploads | unit (fake session) and live 409 with existing id |
| File security | password-protected PDF, macro DOCX, zip bomb, PDF/DOCX renamed `.txt`, binary renamed `.txt`, extension/content mismatch, prompt injection and delimiter spoofing, no JD text in logs, download as attachment with sandbox CSP |
| Frontend upload and display | `JobDescriptionWorkspace.test.tsx` (9): upload → poll → required vs preferred, experience, evidence, warnings, download link; client validation; server rejection; failure + retry; review save (evidence dropped for edited values); invalid years blocked; delete; config error. `jobDescriptions.test.ts` (21) |
| Real provider | `test_llm_provider_live.py` (resume + JD), skipped without a key |

### Test counts (collected)

| File | Tests |
|---|---|
| `tests/unit/test_jd_documents.py` | 29 |
| `tests/unit/test_jd_structuring.py` | 35 |
| `tests/unit/test_jd_api.py` | 21 |
| `tests/unit/test_jd_processor.py` | 6 |
| `tests/unit/test_migrations_offline.py` (now 3 revisions) | 24 |
| `tests/integration/test_jd_workflow_live.py` | 22 |
| `tests/integration/test_llm_provider_live.py` | 2 (skipped) |
| `frontend/src/lib/jobDescriptions.test.ts` | 21 |
| `frontend/src/components/job-description/JobDescriptionWorkspace.test.tsx` | 9 (8 workspace + 1 JDView) |

### Live end-to-end run (real servers, no test doubles)

| Action | Result |
|---|---|
| Upload `role.txt` | 202 → `txt`, 600 chars → `failed` / `llm_not_configured`, `title` null |
| Upload `role.pdf` | 202 → `pdf_text`, 1 page, 614 chars → `llm_not_configured` |
| Upload `role-scanned.pdf` (image only) | 202 → **`pdf_ocr`**, 281 chars → `llm_not_configured` |
| Upload `role.docx` | 202 → `docx`, 610 chars → `llm_not_configured` |
| `GET /{id}/parsed` | 409 `parse_not_completed` |
| Re-upload `role.txt` | 409 `duplicate_job_description` with existing id |
| Upload password-protected PDF | 422 `encrypted_file` |
| Upload binary renamed `binary.txt` | 415 `content_mismatch` |
| `GET ?limit=10` | `total: 4` |
| Download TXT | 200, `text/plain; charset=utf-8`, attachment, bytes identical |
| Delete PDF and DOCX JDs | 204 each → GET 404; files removed |
| Log scan: company / soft skill / location | 0 / 0 / 0 |
| Frontend `GET /job-descriptions` | 200 with upload form |
| CORS preflight `PUT …/parsed` from `http://localhost:3000` | 200, origin allowed |

### Coverage gaps (honest)

- `job_descriptions.py` routes: 88%. Not exercised: disk-write failure on upload, the IntegrityError race on concurrent duplicate uploads, a stored file missing on download, and an error while deleting a stored file.
- JD processor: 87%. The unexpected-exception path and the missing-storage-path branch are not exercised.

---

## Phase 1A: Resume Parsing Engine (2026-11-12)

**Environment:** your PostgreSQL 18 (127.0.0.1:5432; live tests use random temporary schemas that are dropped afterwards), Memurai 8.2, Tesseract 5.4.0 (`eng`), Python 3.12.16, Node 24. **No `GROQ_API_KEY` configured.**

### Summary

| Check | Command | Result |
|---|---|---|
| Backend format | `uv run ruff format --check .` | ✅ 72 files already formatted |
| Backend lint (incl. bandit) | `uv run ruff check .` | ✅ All checks passed |
| Backend types | `uv run mypy` (strict) | ✅ No issues in 71 source files |
| Backend unit tests | `uv run pytest -q --cov` | ✅ **391 passed** |
| Backend live tests | `INTEGRATION_DATABASE_URL=… INTEGRATION_REDIS_URL=… uv run pytest -m integration` | ✅ **48 passed** |
| Backend combined | `uv run pytest -m "" --cov` | ✅ **439 passed, 96% coverage** |
| Migration on your DB | `uv run alembic upgrade head` / `current` / `check` | ✅ `0001 → 0002_resume_parsing (head)`; no drift |
| Frontend lint | `npm run lint` | ✅ 0 problems |
| Frontend types | `npm run typecheck` | ✅ 0 errors |
| Frontend tests | `npm test` | ✅ **94 passed** (8 files) |
| Frontend build | `npm run build` | ✅ `/`, `/_not-found`, `/resumes` prerendered |
| **Real Groq structuring** | | ⛔ **NOT RUN**: no API key |

### Requested test categories → evidence

| Category | Tests | Where |
|---|---|---|
| Valid PDF and DOCX | validation (4), extraction (5), API 202, live PDF→DB and DOCX→DB | `test_resume_validation.py`, `test_resume_extraction.py`, `test_resume_api.py`, `test_resume_workflow_live.py` |
| Scanned PDF | real Tesseract OCR unit test; live scanned PDF → OCR → LLM → DB; OCR disabled or missing → `ocr_unavailable` | extraction and live tests |
| Corrupted and empty files | empty, random bytes, header-only, truncated (33%, 66%, 90%), DOCX missing its document part, corrupted ZIP, blank PDF → `no_text`, empty DOCX → `no_text` | validation, extraction, API, live |
| Invalid LLM responses | non-JSON, always invalid, 7 schema violations (unknown keys, wrong types, missing skill name, bad enum, non-object), corrective-retry success, code-fenced JSON, malformed provider envelopes | `test_resume_structuring.py`, `test_llm_client.py`, live |
| LLM timeout | client `ReadTimeout` → `LLMTimeoutError`; structurer → `llm_timeout`; live → stored `llm_timeout` with text kept | client, structuring, live |
| Missing resume information | minimal JSON → null and empty lists; `N/A`/`""` become null; frontend "No … found in the resume" for all 7 sections | structuring, `ResumeWorkspace.test.tsx` |
| Database failures | DB down on lookup → 503 (no driver details); DB down on commit → 503 and stored file removed; DB down at processor start, mid-save, and while recording a failure → logged, never crashes | `test_resume_api.py`, `test_resume_processor.py` |
| Security and file validation | encrypted PDF, macro-enabled DOCX, zip bomb, extension/content mismatch, plain ZIP, path traversal in filenames, 9 unsafe storage keys, prompt injection kept inside delimiters, delimiter spoofing, grounding removes invented contact details and skills, no API key in errors or logs, no resume text in logs, download as attachment with sandbox CSP | validation, storage, structuring, client, processor, live |

### New test files

| File | Tests |
|---|---|
| `tests/unit/test_resume_validation.py` | 39 |
| `tests/unit/test_resume_extraction.py` | 17 |
| `tests/unit/test_llm_client.py` | 20 |
| `tests/unit/test_resume_structuring.py` | 32 |
| `tests/unit/test_resume_api.py` | 18 |
| `tests/unit/test_resume_processor.py` | 6 |
| `tests/unit/test_resume_storage.py` | 14 |
| `tests/unit/test_config.py` (LLM/resume settings) | +11 |
| `tests/integration/test_resume_workflow_live.py` | 19 |
| `frontend/src/lib/resumes.test.ts` | 21 |
| `frontend/src/components/resume/ResumeWorkspace.test.tsx` | 8 (7 workspace + 1 ParsedResumeView) |

### Live end-to-end run (real servers, no test doubles)

`uvicorn` on your `backend/.env`, plus the production frontend; synthetic files sent with `curl`:

| Action | Result |
|---|---|
| `GET /ready` | 200: postgres ok, redis ok |
| `GET /system/capabilities` | `llm.configured=false`, `ocr.available=true` |
| Upload text PDF | 202 → `pdf_text`, 1 page, 517 chars → `failed` / `llm_not_configured` |
| Upload scanned PDF | 202 → **`pdf_ocr`**, 513 chars → `failed` / `llm_not_configured` |
| Upload DOCX | 202 → `docx`, 527 chars → `failed` / `llm_not_configured` |
| Upload `%PDF-1.7 broken` | 422 `corrupted_file` |
| Re-upload same PDF | 409 `duplicate_resume` with the existing id |
| Download original | 200, attachment, sandbox CSP, `nosniff`, bytes identical |
| Delete DOCX resume | 204; then GET → 404; stored file removed |
| CORS preflight DELETE from `http://localhost:3000` | 200, origin allowed |
| Backend log scan for email / phone / company | 0 / 0 / 0 |
| `GET /resumes` (frontend) | 200 with the upload form |

### Defects found by tests and fixed

1. Truncated PDFs passed validation, because MuPDF silently repairs them.
2. `ResumeStorage.save` leaked a raw `OSError`.
3. The production build failed: a function-bearing object was passed to a client component.
4. The file input's accessible name included its hint text.
5. A frontend test script expected a third poll that the component correctly never makes. This was a test bug.
6. The scanned-PDF fixture exceeded 10 MB (no deflate). This was a fixture bug.

### Coverage gaps (honest)

`resumes.py` is at 90%. Not exercised: a disk-write failure during upload, the IntegrityError race on concurrent duplicate uploads, a missing stored file on download, and an error while deleting a stored file. `extraction.py` is at 92%: Linux tessdata paths and the OCR-failure branch.

---

## Phase 0: verification on your PostgreSQL (2026-11-12)

After you created `candidate_intel` (owner `candidate_app`, UTF8, PUBLIC access revoked)
and applied the migration, I re-ran the checks against **your** PostgreSQL 18 on
127.0.0.1:5432, using `backend/.env`:

| Check | Result |
|---|---|
| `uv run alembic current` | `0001_initial_schema (head)` |
| `uv run alembic check` | `No new upgrade operations detected.` |
| `scripts\integration-test.ps1` | PostgreSQL and Redis reachable; **29 passed** |
| `uv run pytest --cov` | 228 passed, 98% coverage |
| `npm test` (frontend) | 64 passed |

---

## Phase 0, revision 2: models, migrations, layout (2026-11-12)

**Database used for live tests:** a **throwaway PostgreSQL 18 instance** that I created with
`initdb` in the session's scratch folder (127.0.0.1:55432, SCRAM authentication, random
passwords). It was stopped and deleted afterwards. Your PostgreSQL service (5432) was not
used; it was confirmed running at the end. **Redis:** Memurai 8.2 at 127.0.0.1:6379.

### Completion criteria

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | Docker Compose starts the infrastructure | ⛔ **NOT RUN** | You asked that Docker not be used. `docker compose config` (YAML parsing only, no engine) validates the file; it has never been started |
| 2 | Backend starts successfully | ✅ | `uvicorn app.main:create_app --factory` on 127.0.0.1:8000 |
| 3 | Frontend starts successfully | ✅ | `npm run build` passes; standalone `server.js` on 127.0.0.1:3000 serves `/` with HTTP 200, including the header, "How an evaluation works", "System status" and the skip link |
| 4 | PostgreSQL connection works | ✅ | `/ready` → `postgres: ok (56.68 ms)`; live probe test passes |
| 5 | Redis connection works | ✅ | `/ready` → `redis: ok (5.17 ms)`; SET/GET/TTL/DEL round-trip test passes |
| 6 | Health endpoint succeeds | ✅ | `GET /api/v1/health` → 200; `GET /api/v1/ready` → 200 `"status":"ready"` in 0.06 s |
| 7 | Database migrations run | ✅ | `alembic upgrade head` → `Running upgrade -> 0001_initial_schema`; `alembic current` → `0001_initial_schema (head)`; `alembic check` → `No new upgrade operations detected` |
| 8 | Automated tests pass | ✅ | Backend 228 unit + 29 live = **257 passed**; frontend **64 passed** |

### Exact commands used

```bash
# --- static checks (backend/) ---
uv run ruff format --check .        # 49 files already formatted
uv run ruff check .                 # All checks passed!
uv run mypy                         # Success: no issues found in 48 source files

# --- backend unit tests (backend/) ---
uv run pytest -q --cov              # 228 passed, 29 deselected; coverage 98% (730 stmts)

# --- offline migration checks (backend/), no database needed ---
uv run alembic heads                # 0001_initial_schema (head)
uv run alembic upgrade head --sql   # CREATE TABLE x5, CREATE INDEX x5, version insert
uv run alembic downgrade 0001_initial_schema:base --sql

# --- throwaway PostgreSQL (PowerShell), not your server ---
initdb -D <scratch>\pgtest\data -U postgres --pwfile=<random> --auth=scram-sha-256 -E UTF8 --locale=C
pg_ctl -D <scratch>\pgtest\data -o "-p 55432 -c listen_addresses=127.0.0.1" -w start
$env:PGPASSWORD = <random superuser pw>
scripts\setup-local-db.ps1 -PgPort 55432   # in a scratch copy; run twice (idempotent)

# --- real migrations (backend/), DATABASE_URL from the scratch backend/.env ---
uv run alembic current              # (none)
uv run alembic upgrade head         # Running upgrade  -> 0001_initial_schema
uv run alembic current              # 0001_initial_schema (head)
uv run alembic check                # No new upgrade operations detected.

# --- live integration tests (backend/) ---
INTEGRATION_DATABASE_URL=<scratch url> INTEGRATION_REDIS_URL=redis://127.0.0.1:6379/0 \
  uv run pytest -m integration -v   # 29 passed

# --- frontend (frontend/) ---
npm run lint                        # 0 problems
npm run typecheck                   # next typegen && tsc --noEmit: 0 errors
npm test                            # 6 files, 64 passed
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000 npm run build   # Compiled successfully

# --- end to end ---
uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
node .next/standalone/server.js     # PORT=3000
curl http://127.0.0.1:8000/api/v1/health     # 200
curl http://127.0.0.1:8000/api/v1/ready      # 200 ready, postgres ok, redis ok
curl -X OPTIONS -H "Origin: http://127.0.0.1:3000" -H "Access-Control-Request-Method: GET" \
  http://127.0.0.1:8000/api/v1/ready         # 200, access-control-allow-origin matches
curl http://127.0.0.1:3000/                  # 200
```

### Live test results (29/29 passed)

| File | Tests | What is verified |
|---|---|---|
| `test_live_services.py` | 5 | Postgres probe (app role is **not** a superuser), wrong DB password gives `InvalidPasswordError` with no leak, Redis probe and round-trip, bad Redis credentials, `/ready` 200 through the real lifespan |
| `test_migrations_live.py` | 4 | upgrade creates tables and records the revision; **no drift between migration and models** (`compare_metadata` = []); downgrade to base and re-upgrade; upgrade at head is a no-op |
| `test_models_live.py` | 20 | inserts with relationships and defaults; DB-generated UUID for raw SQL; enums stored lowercase; deleting a candidate cascades (JD kept); JD delete blocked by RESTRICT; deleting a resume sets `evaluation.resume_id` to NULL; 9 CHECK/FK violations rejected; 3 UNIQUE violations rejected; ORM rejects an invalid enum before sending; NUMERIC score round-trip |

Isolation check after the run: **0 leftover `test_*` schemas**. The migrated `public` schema
was untouched (0 rows). Role `candidate_app`: superuser = false, createdb = false.

### New unit tests

| File | Tests | Covers |
|---|---|---|
| `test_models.py` | 54 | exactly 5 tables; UUID PK with server fallback on each; timestamps; unique Python-side UUIDs; FK targets and delete rules; nullability; 14 named constraints; enums stored as values; status server defaults; JSONB; relationship cascade/back-populate; `repr` free of PII |
| `test_schemas.py` | 39 | valid, blank, oversized and missing fields; e-mail normalisation and rejection; unknown fields; http(s)-only URLs; unsafe usernames (`../`, `/`, `?`, spaces); unknown platform; ORM → Read conversion; unavailable kept distinct; unscored is null; score range |
| `test_migrations_offline.py` | 16 | single linear head; every table created; dependency order; key DDL fragments; downgrade drops in reverse order |
| `test_db.py` | 5 (+1) | composite unique naming; test tables no longer leak into app metadata |
| Frontend `apiClient.test.ts` | 13 | URL joining, versioned paths, error envelope parsing, bound client |
| Frontend `Layout.test.tsx` | 3 | header brand and navigation, API docs link (`noopener noreferrer`), footer |
| Frontend `page.test.tsx` | +1 | four evaluation steps in order |

### Defects found and fixed

1. A blank e-mail was normalised to `None`, after which the `max_length` constraint crashed
   on it (`TypeError`). Found by `test_blank_optional_fields_become_none`.
2. The `test_db.py` throwaway tables were registered on the application's `Base.metadata`.
   They would have shown up as false drift in the migration-vs-models check.

### Known warnings (unchanged)

- 1 Starlette `TestClient` deprecation warning (`httpx` → `httpx2`). It affects tests only.
- `npm audit`: 0 vulnerabilities in production dependencies; 7 moderate in the dev-only
  ESLint toolchain.

---

## Phase 0, revision 1: native services, no Docker (2026-11-11)

Docker is not available on the development machine, so Docker is no longer part of the
workflow. Local services are the **PostgreSQL 18** Windows service (127.0.0.1:5432) and
**Memurai 8.2** (127.0.0.1:6379), a Windows service compatible with Redis 8.2. The
Docker-based checks listed in the original Phase 0 run below are **withdrawn, not pending**.

### Summary

| Area | Check | Result |
|---|---|---|
| Backend | `ruff format --check .` / `ruff check .` | ✅ 35 files formatted / all checks passed |
| Backend | `mypy` (strict) | ✅ no issues in 34 source files |
| Backend | `pytest` unit + no-service integration | ✅ **118 passed**, 98% coverage (487 stmts, 7 missed) |
| Backend | Live Redis tests against Memurai (`INTEGRATION_REDIS_URL=redis://127.0.0.1:6379/0`) | ✅ **2 passed**: probe + SET/GET/TTL/DEL round-trip; bad credentials reported as an error without leaking |
| Backend | Live PostgreSQL tests (3) | ⏸ **NOT RUN**: they need the `candidate_app` role and database, created once by `scripts/setup-local-db.ps1`, which prompts for your PostgreSQL superuser password |
| Backend | Real uvicorn against native services (DB not set up yet) | ✅ `/ready` 503 in **0.15 s**: postgres `InvalidPasswordError` (expected before setup), redis `ok` in **5.28 ms**; 0 password occurrences in the log; URL logged as `candidate_app:***REDACTED***@127.0.0.1:5432` |
| Scripts | `setup-local-db.ps1`, `integration-test.ps1` | ✅ 0 parse errors. Setup script exercised in a scratch copy against a closed port: creates `.env`, generates a 32-character password, preserves the other lines, leaves the password unchanged on re-run, and the output loads through `Settings`. Integration script refuses to run without `backend/.env` |
| Frontend | lint / typecheck / tests / build | ✅ unchanged from the original run (47 passed; no frontend code changed) |

### Defects found and fixed in this revision

1. **Redis readiness timed out although Memurai was running.** `localhost` resolves to IPv6
   `::1` first on Windows, and Memurai listens on IPv4 only. Measured: **2056 ms** via
   `localhost` against **2 ms** via `127.0.0.1`. All defaults, examples and scripts now use
   `127.0.0.1`.
2. **PostgreSQL failures were reported as `DBAPIError`.** The real chain is
   `sqlalchemy.exc.DBAPIError` → `sqlalchemy…asyncpg.Error` → `asyncpg.InvalidPasswordError`.
   `root_cause_name()` now reports the innermost type. New unit test:
   `test_sqlalchemy_wrapped_driver_error_reports_root_cause`.
3. **The setup script wrote a URL with an empty host and port.** Later `-match` checks
   overwrote PowerShell's automatic `$Matches` variable. The captures are now copied
   immediately. Found by running the script, before release.
4. **Smoke-test servers kept running.** Stopping by port missed the child processes that
   `uv run` starts, so a stale server answered one check. That result was discarded,
   leftovers were killed by command line, and the check was re-run from scratch.

### Live integration tests (`tests/integration/test_live_services.py`, 5 tests)

| Test | Needs | Status |
|---|---|---|
| `test_postgres_probe_ok` (also asserts the app role is **not** a superuser) | DB setup | ⏸ skipped |
| `test_postgres_wrong_password_is_reported` (`InvalidPasswordError`, no leak) | DB setup | ⏸ skipped |
| `test_redis_probe_ok_and_roundtrip` | Memurai | ✅ passed |
| `test_redis_bad_credentials_are_reported` | Memurai | ✅ passed |
| `test_ready_endpoint_with_real_lifespan` (`/ready` 200) | both | ⏸ skipped |

### To finish Phase 0 verification

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-local-db.ps1    # prompts for the postgres password
powershell -ExecutionPolicy Bypass -File scripts\integration-test.ps1  # expect 5 passed
```

---

## Phase 0: original run (2026-11-11). Docker items superseded by revision 1


**Environment:** Windows 11 Enterprise · Python 3.12.16 (uv 0.13.7, isolated `backend/.venv`)
· Node 24.19.0 / npm 11.17.0 · Docker 29.8.0 CLI (**engine not running during this run**).

### Summary

| Area | Check | Result |
|---|---|---|
| Backend | `ruff format --check .` | ✅ 35 files already formatted |
| Backend | `ruff check .` (incl. bandit `S`, async, bugbear rules) | ✅ All checks passed |
| Backend | `mypy` (strict, pydantic plugin) | ✅ No issues in 34 source files |
| Backend | `pytest` unit + no-Docker integration | ✅ **117 passed**, 4 deselected (live tests), 1 warning |
| Backend | Line + branch coverage | ✅ **98%** (475 stmts, 7 missed) |
| Backend | Live integration tests (`-m integration`) | ⏸ **4 skipped**: Docker engine not running |
| Backend | Real uvicorn smoke test | ✅ see below |
| Backend | `alembic upgrade head --sql` (offline) | ✅ exit 0 (empty migration set) |
| Backend | Startup with missing `DATABASE_URL`/`REDIS_URL` | ✅ fails fast: `2 validation errors for Settings … Field required` |
| Frontend | `npm run lint` (`eslint --max-warnings=0`) | ✅ 0 problems |
| Frontend | `npm run typecheck` (`next typegen && tsc --noEmit`) | ✅ 0 errors |
| Frontend | `npm test` (Vitest + Testing Library, jsdom) | ✅ **47 passed** in 4 files |
| Frontend | `npm run build` (production, standalone) | ✅ Compiled; `/` prerendered static |
| Frontend | Standalone server smoke test | ✅ see below |
| Frontend | `npm audit --omit=dev` | ✅ 0 vulnerabilities in production deps |
| Infra | `docker compose --env-file .env.example config --quiet` | ✅ valid |
| Infra | Compose with secrets missing | ✅ refuses: `required variable POSTGRES_PASSWORD is missing a value` |
| Infra | `.gitignore` rules (`git check-ignore`, 16 paths) | ✅ secrets/PII ignored; `.env.example` files tracked |
| Infra | `scripts/integration-test.ps1` | ✅ parses (0 errors); Docker-down path prints a clear error, exit 1 |
| Infra | Image builds, container health checks, hardening at runtime | ⏸ **NOT RUN**: Docker engine not running |

### Backend tests by file

| File | Tests | Covers |
|---|---|---|
| `tests/unit/test_config.py` | 39 | valid/env loading, secret masking, immutability, missing fields, invalid URLs/levels/platforms/CORS/prefix/ranges, production guards (debug, wildcard CORS, DB_ECHO, placeholder passwords) |
| `tests/unit/test_logging.py` | 23 | redaction of URL credentials, bearer tokens, GitHub/Groq/OpenRouter keys, key=value secrets, nested `extra` keys, tracebacks; JSON fields; request ID; malformed format args; idempotent configuration |
| `tests/unit/test_middleware_and_errors.py` | 25 | request ID generate/propagate/reject injection; access log fields; 404/405/422/500 envelopes; 422 does not echo input; 500 hides internals **and keeps request-ID/CORS/security headers**; CORS allow/deny/expose; docs disabled in production |
| `tests/unit/test_health_service.py` | 13 | probe ok / refused / invalid password (no detail leak) / query failure / timeout; Redis ok / connection / auth error / timeout / falsy PING; cancellation propagates |
| `tests/unit/test_health_api.py` | 10 | `/health` ok and does not touch dependencies; custom prefix; `/ready` all ok, Postgres down, Redis down, both down, both time out (bounded under 1 s, concurrent), no credentials in body |
| `tests/unit/test_db.py` | 4 | naming convention in DDL, timezone-aware server-default timestamps, lazy engine with hidden parameters, session dependency lifecycle |
| `tests/integration/test_real_drivers_unreachable.py` | 3 | **real** asyncpg and redis drivers against closed ports; full app lifespan via `TestClient`, giving `/health` 200 and `/ready` 503 |
| `tests/integration/test_live_services.py` | 4 (skipped) | live Postgres probe and `SHOW server_version`, Redis probe plus SET/GET/DEL round-trip, wrong Redis password reported without leaking, `/ready` 200 through the real lifespan |

Coverage gaps (7 lines): the CSV parser's non-string branch in `config.py`, the
last-resort `ServerErrorMiddleware` handler and the non-HTTP scope passthrough in
`errors.py`, and the non-JSON `exc_info` branch in `TextFormatter`.

### Frontend tests by file

| File | Tests | Covers |
|---|---|---|
| `src/lib/config.test.ts` | 10 | default/blank, http(s), trailing slash, query/fragment stripped; rejects bare host, garbage, `ftp:`, `javascript:`, `file:`, embedded credentials |
| `src/lib/status.test.ts` | 26 | schema validation (valid, null, array, wrong types, unknown enums, missing optional fields become null); fetch success with request ID, 503 readiness accepted, network failure, timeout abort, non-JSON, error envelope, generic HTTP error, 503 not accepted on `/health`, schema mismatch |
| `src/components/StatusPanel.test.tsx` | 9 | operational, degraded, backend unreachable (dependencies shown **Unknown**, not down), invalid response, refresh re-fetch, invalid base URL (no requests sent), loading state, missing dependency, timeout tone |
| `src/app/page.test.tsx` | 2 | heading, capabilities shown as "Planned", status panel renders; planned features not presented as active |

### Live smoke tests (no Docker)

**Backend.** `uvicorn app.main:create_app --factory --port 8011` was run with
`DATABASE_URL`/`REDIS_URL` pointing at closed ports:

- `GET /api/v1/health` returned **200**, with all 5 security headers and an `x-request-id`.
- `GET /api/v1/ready` with `X-Request-ID: smoke-1` returned **503** and echoed `x-request-id: smoke-1`.
  Body: `postgres` and `redis` `"status":"timeout"`, `"latency_ms":null`. On Windows, a
  closed local port waits for TCP retries, so it surfaces as a timeout.
- `GET /nope` returned **404** with the error envelope and `request_id`.
- Server log: JSON lines. The DB URL was logged as `postgresql+asyncpg://u:***REDACTED***@127.0.0.1:5999/db`.
  `grep` for both test passwords found **0 occurrences**.

**Frontend.** `node .next/standalone/server.js` was run on port 3011:

- `GET /` returned **200** with `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, `Permissions-Policy` and `Cross-Origin-Opener-Policy`, and no `X-Powered-By`.
- The HTML contains the `Candidate Intelligence` heading, the `System status` panel and the
  `Planned` badges.

### Warnings and known issues found during testing

1. **StarletteDeprecationWarning** (backend tests, 1 warning): Starlette 1.10 recommends
   the `httpx2` package for `TestClient`. It affects test tooling only. Adding `httpx2`
   was blocked by the session's permission policy, so it is left as a follow-up.
2. **`npm audit` (dev dependencies only): 7 moderate** findings, all in `minimatch`, pulled
   in by the ESLint toolchain. Production dependencies have 0. Not force-fixed, because
   `npm audit fix --force` would make breaking changes to the lint toolchain.
3. Fixed during this phase (found by the tests and checks):
   - Unhandled 500 responses lost the request ID, CORS and security headers. Fixed with
     `UnhandledExceptionMiddleware`.
   - uvicorn's ANSI `color_message` field leaked into the JSON logs. Now excluded.
   - The scaffold's `@types/node@^20` conflicted with Vitest 5. Aligned to `^24`.
   - The `LayoutProps` type was missing for a standalone `tsc`. Fixed by running `next typegen`.
   - PowerShell 5.1 treated native stderr as a terminating error in the integration script.
     Now uses exit-code checks.

### ~~Remaining Docker-dependent verification~~: withdrawn (Docker is not used, see revision 1)

```powershell
docker version --format "{{.Server.Version}}"
Copy-Item .env.example .env        # set real passwords first
powershell -ExecutionPolicy Bypass -File scripts\integration-test.ps1   # 4 live tests
docker compose up -d --build
docker compose ps                  # expect all 4 services (healthy)
curl.exe -i http://127.0.0.1:8000/api/v1/ready   # expect 200 "ready"
curl.exe -I http://127.0.0.1:3000/
docker compose exec backend id     # expect uid=10001
```

Still to confirm in that run:

- both images build, including the frontend's Linux/musl native packages from the
  Windows-generated lockfile;
- the postgres and redis entrypoints work with `cap_drop: ALL` plus the listed caps and a
  read-only root filesystem;
- the backend healthcheck works inside the container;
- the 4 live integration tests pass.
