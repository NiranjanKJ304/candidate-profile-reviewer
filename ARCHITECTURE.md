# Architecture

## 1. Overview

```
 Browser ──► Next.js frontend (:3000) ──fetch──► FastAPI backend (:8000)
                                                     │
                                   ┌─────────────────┼──────────────────┐
                                   ▼                 ▼                  ▼
                             PostgreSQL 18        Memurai 8.2   External platform APIs
                             (SQLAlchemy async)   (Redis-compat.,  (later phases, official
                             127.0.0.1:5432        127.0.0.1:6379)  / permitted sources only)
```

In Phase 0 the frontend calls only `/api/v1/health` and `/api/v1/ready`, from the browser.
The backend talks only to PostgreSQL and Redis.

### Runtime environment

Docker is not available on the development machine, so every component runs natively:

| Component | How it runs locally |
|---|---|
| Backend | `uv run uvicorn app.main:create_app --factory`, using the uv-managed Python 3.12 virtualenv |
| Frontend | `npm run dev`, or `npm run build` followed by the standalone `server.js` |
| PostgreSQL 18 | Windows service. The app connects as the least-privilege `candidate_app` role, created by `scripts/setup-local-db.ps1` |
| Redis | **Memurai 8.2**, a Windows service compatible with Redis 8.2. The app uses the standard `redis` client unchanged |

Connection URLs use `127.0.0.1`, not `localhost`. On Windows, `localhost` tries IPv6 `::1`
first, and Memurai listens on IPv4 only. We measured 2056 ms per new connection via
`localhost` against 2 ms via `127.0.0.1`.

## 2. Backend structure

```
backend/app/
├── main.py              create_app() factory: logging, middleware, routers, lifespan
├── core/                cross-cutting concerns, no business logic
│   ├── config.py        Settings (pydantic-settings), Platform enum, production guards
│   ├── logging.py       JSON/text formatters, secret redaction filter
│   ├── middleware.py    request ID + access log, security headers (pure ASGI)
│   ├── errors.py        error envelope, exception handlers, catch-all middleware
│   ├── context.py       request-scoped ContextVar (request_id)
│   └── enums.py         domain enums (Platform, statuses), shared by config, models, schemas
├── api/v1/              HTTP layer only: routing, request/response schemas
├── schemas/             Pydantic schemas: common (health, errors) and domain (create/read)
├── services/            business logic (Phase 0: health probes)
├── db/                  SQLAlchemy Base, mixins, naming conventions, engine/session factories
├── models/              ORM models: Candidate, Resume, JobDescription, DeveloperProfile, Evaluation
├── cache/               Redis client factory
└── integrations/        external platform API clients (empty in Phase 0)
```

### Layering rules

1. `api` → `services` → (`db`, `cache`, `integrations`). Dependencies never point upward.
2. `integrations/` contains only API clients that return typed Pydantic models. They hold
   no scoring or evaluation logic, so platform changes stay isolated from business rules.
3. Every external input is validated with Pydantic: HTTP bodies, environment variables,
   and (later) platform API responses and LLM outputs.
4. Dependencies (`get_engine`, `get_redis`, `get_session`, `get_settings`) are injected
   through FastAPI `Depends`, so tests substitute fakes without monkeypatching.

### Application lifecycle

- `create_app(settings)` is a factory (`uvicorn app.main:create_app --factory`). Settings
  are loaded at startup rather than at import, which keeps tests isolated.
- The lifespan creates the async engine and the Redis client. Neither opens a connection
  eagerly, so the process starts and reports "not ready" while dependencies are down.
  On shutdown, Redis is closed and the engine is disposed.

### Middleware order (outermost first)

```
RequestContextMiddleware   assigns/propagates X-Request-ID, writes the access log
SecurityHeadersMiddleware  nosniff, frame DENY, no-referrer, COOP, Permissions-Policy
CORSMiddleware             allow-list from CORS_ORIGINS, exposes X-Request-ID
UnhandledExceptionMiddleware  converts unexpected exceptions into a 500 envelope
  └── FastAPI exception handlers (HTTP errors, validation) → routers
```

Starlette routes `Exception` handlers to `ServerErrorMiddleware`, which wraps *all* user
middleware, so a 500 from there would lose the request ID, CORS and security headers.
The innermost `UnhandledExceptionMiddleware` keeps those layers in the response path.
This is covered by `test_500_is_generic_and_keeps_headers`.

### Health model

| Endpoint | Purpose | Touches dependencies | Status codes |
|---|---|---|---|
| `/api/v1/health` | Liveness: the process serves requests | No | 200 |
| `/api/v1/ready` | Readiness: PostgreSQL and Redis are reachable | Yes, concurrently, each bounded by `READINESS_TIMEOUT_SECONDS` | 200 / 503 |

Probe failures report only the exception *type* (for example `ConnectionRefusedError`),
never the message, so hostnames and credentials cannot leak to clients. SQLAlchemy wraps
driver errors (`DBAPIError` → adapter error → asyncpg error), so the probe reports the
innermost cause. That gives `InvalidPasswordError` rather than the uninformative
`DBAPIError`.

## 3. Configuration and secrets

- All configuration comes from environment variables, optionally via a git-ignored `.env`.
- Credentials (`DATABASE_URL`, `REDIS_URL`) are `SecretStr`, which masks them in `repr`,
  in `model_dump` and in logs.
- Validation at startup checks the async driver scheme, the URL structure, numeric ranges,
  CORS origins, platform names and the API prefix format.
- Production guards reject `DEBUG=true`, wildcard CORS, `DB_ECHO=true` and `change-me`
  placeholder passwords.
- OpenAPI/Swagger UI is disabled when `ENVIRONMENT=production`.

## 4. Logging

- One JSON object per line: `timestamp`, `level`, `logger`, `message`, `request_id`,
  plus structured `extra` fields.
- `RedactionFilter` runs before formatting and masks:
  - URL credentials (`scheme://user:***@host`);
  - Bearer/Basic tokens;
  - GitHub, Groq and OpenRouter key shapes;
  - `password=`/`token:`-style pairs;
  - any `extra` key containing password, secret, token, api_key, authorization, cookie,
    credential, dsn and similar words;
  - exception tracebacks.
- Uvicorn's own access log is disabled in favour of `app.access`, which records method,
  path, status, duration and request ID.
- `LOG_JSON=false` switches to a readable text format for local development.

## 5. Data layer

- SQLAlchemy 2.x async with asyncpg, `pool_pre_ping`, a connect timeout, and
  `hide_parameters` unless debug mode is on.
- `Base` uses a deterministic constraint naming convention so Alembic migrations are stable.
- `TimestampMixin` provides `created_at`/`updated_at` columns with time zones, set by the
  database. Provenance records in later phases will build on it.
- `UUIDPrimaryKeyMixin`: UUID primary keys generated by the application (`uuid4`), with
  `gen_random_uuid()` as a database-side fallback for raw SQL inserts.
- `string_enum()`: statuses are stored as `VARCHAR(20)` plus a named CHECK constraint
  rather than native PostgreSQL enums. Adding a value later only changes a constraint, and
  the database holds the lowercase value (`"pending"`), not the Python member name.
- JSON columns are `JSONB` on PostgreSQL.
- Alembic `env.py` reads the URL from application settings, so the URL is never stored in
  `alembic.ini`. It also accepts an existing connection, which tests use to migrate an
  isolated temporary schema.

### Domain model (migration `0001_initial_schema`)

```
                    ┌──────────────┐
          ┌─────────│  candidates  │──────────┐
          │ CASCADE └──────┬───────┘ CASCADE  │
          ▼                │ CASCADE          ▼
   ┌────────────┐          │         ┌────────────────────┐
   │  resumes   │          │         │ developer_profiles │
   └─────┬──────┘          ▼         └────────────────────┘
         │ SET NULL  ┌─────────────┐
         └──────────►│ evaluations │◄──── RESTRICT ──── job_descriptions
                     └─────────────┘
```

| Table | Key columns | Constraints |
|---|---|---|
| `candidates` | `full_name`, `email` (optional, unique, stored lower-case), `phone`, `location` | full name not blank |
| `resumes` | `original_filename`, `content_type`, `file_size_bytes`, `sha256`, `storage_path`, `parse_status`, `parsed_data` (JSONB), `parse_error`, `parsed_at` | size > 0; sha256 is 64 lower-case hex characters; unique (candidate, sha256), so the same file cannot be stored twice |
| `job_descriptions` | `title`, `company`, `description_text`, `source_url`, `analysis` (JSONB) | title and text not blank |
| `developer_profiles` | `platform`, `username`, `profile_url`, `ownership_status`, `collection_status`, `collected_data` (JSONB), `source_url`, `collected_at` | one profile per (candidate, platform); platform and statuses limited to known values |
| `evaluations` | `status`, `overall_score` (NUMERIC 5,2), `result` (JSONB), `model_name`, `error_message`, `completed_at` | score is NULL or between 0 and 100 |

Design rules built into the schema:

- **Right to erasure:** deleting a candidate cascades to their resumes, profiles and
  evaluations, both in the database (`ON DELETE CASCADE`) and in the ORM.
- **History is kept:** a job description that has evaluations cannot be deleted
  (`RESTRICT`). Deleting a resume keeps its evaluations, with `resume_id` set to NULL.
- **Unavailable is not zero:** `collection_status = unavailable` is separate from collected
  data, and an unscored evaluation has `overall_score = NULL`, never 0.
- **Ownership is separate from collection:** `ownership_status` (unverified, verified,
  mismatch) records whether a profile is the candidate's, independently of whether data was
  collected.
- **Provenance:** profiles store `source_url` and `collected_at`. Evaluations store
  `model_name` and `resume_id`.
- `repr()` of models never includes personal data, because model objects can appear in logs.

API schemas (`app/schemas/domain.py`) validate input before it reaches the models:
- whitespace is stripped and lengths are bounded;
- e-mail addresses are normalised to lower case;
- usernames are limited to `[A-Za-z0-9._-]`, which blocks path or URL injection;
- URLs must be absolute http(s);
- unknown fields are rejected.

No endpoints use these schemas yet.

## 6. Frontend

- Next.js 16.4 (App Router, Turbopack, Cache Components), React 19, Tailwind CSS 4.
- `src/lib/config.ts` validates `NEXT_PUBLIC_API_BASE_URL`. Only http(s) URLs without
  credentials are accepted; an invalid value shows a configuration error and no requests
  are sent. The value is inlined at build time, so it must never contain secrets.
- `src/lib/apiClient.ts` is the shared HTTP client: it builds versioned URLs, applies timeouts and parses the error envelope. `createApiClient(baseUrl)` returns a client bound to one backend.
- `src/lib/status.ts` holds the health payload types and parsers on top of it. It is a typed client that validates every response at runtime. It
  separates three outcomes: **unreachable** (network error or timeout), **invalid**
  (non-JSON, unexpected status, or schema mismatch) and **ok**.
- `StatusPanel` (a client component) shows API, PostgreSQL and Redis status. When the
  backend cannot be reached, dependencies are shown as **Unknown**, never as "down". This
  keeps unavailable information distinct from a known failure.
- Security headers come from `next.config.ts`. `poweredByHeader` is disabled, and
  `output: "standalone"` produces a self-contained `server.js` deployment.

## 7. Containers (optional, unused, unverified)

Docker is not used. The compose file and Dockerfiles below are kept only as a starting
point should containers be adopted later. **They have never been built or run.**

| Service | Image | User | Hardening (as written, not verified) |
|---|---|---|---|
| postgres | `postgres:17-alpine` | entrypoint drops to `postgres` | read-only rootfs, tmpfs for `/var/run/postgresql` and `/tmp`, `cap_drop: ALL` plus the 5 caps its entrypoint needs, scram-sha-256 |
| redis | `redis:7.4-alpine` | entrypoint drops to `redis` | read-only rootfs, `--requirepass`, AOF persistence, `cap_drop: ALL` plus CHOWN/SETUID/SETGID |
| backend | multi-stage `python:3.12-slim` with uv | uid 10001, no shell | read-only rootfs, `cap_drop: ALL`, root-owned code, healthcheck on `/api/v1/health` |
| frontend | multi-stage `node:24-alpine`, standalone | `node` (uid 1000) | read-only rootfs, tmpfs `.next/cache`, `cap_drop: ALL`, healthcheck on `/` |

Every service sets `no-new-privileges`, binds its host port to 127.0.0.1, uses log
rotation and has a health check. Startup order is gated by `depends_on:
condition: service_healthy`.

## 8. Phase roadmap

Each phase is built and tested on its own. Later phases are not started without approval.

| Phase | Scope | Status |
|---|---|---|
| 0 | Foundation: config, logging, health, DB/cache wiring, domain models plus initial migration, frontend layout and API client, tests, docs (native services, no Docker) | **Implemented and verified** against native PostgreSQL 18 and Memurai (see TEST_REPORT.md, revision 2). Docker Compose has been written but never run |
| 1A | Resume parsing engine: upload, validation, extraction, OCR, LLM structuring, storage, APIs, upload UI | **Implemented, not complete**: awaiting one real Groq run (see PROJECT_STATUS.md) |
| 1B | Job-description parsing: PDF/DOCX/TXT upload, OCR, dedicated LLM prompt with verbatim evidence, required vs preferred skills, grounding, human review and correction | **Implemented, not complete**: awaiting one real Groq run (see PROJECT_STATUS.md) |
| 2 | GitHub developer-profile integration (REST, evidence with provenance, sync, UI) | **Complete and verified** (incl. real GitHub API) |
| 3 | Codeforces developer-profile integration (official public API, analytics, provenance, sync, UI) | **Complete and verified** (incl. real Codeforces API) |
| 4 | LeetCode feasibility: no permitted API exists, so a manual self-reported evidence workflow is used (never contacts LeetCode) | **Complete and verified** (manual mode; no real LeetCode API test, by design) |
| 5+ | Other platforms (one per phase), aggregation, matching, LLM evaluation, reporting, interview questions | Not started; scope agreed per phase |

### GitHub integration (Phase 2)

```
POST /candidates/{id}/github  -> validation.parse_github_profile()  (username only; URLs never fetched)
   -> developer_profiles row (unverified, sync pending) -> 202
   -> background GitHubSyncService.sync()
        -> GitHubCollector.collect(username)
             GitHubClient (fixed https://api.github.com, token header optional)
             /users/{u} -> /users/{u}/repos (Link pages, cap) -> /repos/{o}/{r}/readme (top N)
             -> /users/{u}/events/public -> /search/issues (PRs, issues)
             each section: status + note + provenance (source URL, API call, time)
        -> normalize.* (no e-mails, no commit messages, bounded excerpts)
        -> row: collected_data snapshot, collected_at, external_id, sync_status, warnings
```

- Transport (`integrations/github.py`), validation, normalisation, collection and sync (`services/github/`), schemas and routes are separate.
- Rate limits: the primary limit is never waited out (sections are marked `rate_limited`); short secondary limits and 5xx are retried.
- A failed retry keeps the last successful snapshot.
- `ENABLED_PLATFORMS` gates all outbound platform calls.

### Codeforces integration (Phase 3)

```
POST /candidates/{id}/codeforces -> validation.parse_codeforces_profile()  (handle only; URLs never fetched)
   -> developer_profiles row (platform codeforces, unverified, sync pending) -> 202
   -> background CodeforcesSyncService.sync()
        -> CodeforcesCollector.collect(handle)
             CodeforcesClient (fixed https://codeforces.com/api/, allow-list of 3 public methods,
                               one shared pacer: lock + >= 2 s between requests)
             user.info (required) -> user.rating -> user.status (from/count, limit + 1, page guard)
             each section: status + note + provenance (source URL, API method, time)
        -> normalize.* (drops e-mail, VK/OpenID, avatar, team-mates; no source code requested)
        -> analytics.submission_stats() (collected window) + lifetime values (profile, user.rating)
        -> row: collected_data snapshot, collected_at, external_id (current handle), sync_status, warnings
```

- Same separation as GitHub: transport (`integrations/codeforces.py`); validation, normalisation, analytics, collection and sync (`services/codeforces/`); schemas; routes.
- No migration: `developer_profiles` stores everything. Codeforces has no numeric id, so `external_id` is the current handle in lower case.
- `status: FAILED` maps to typed errors. "Call limit exceeded", 429, 5xx, timeouts and network errors are retried a bounded number of times. After a rate limit, later methods are skipped.
- Missing submissions give `null` statistics, never zeros. A failed sync keeps the last successful snapshot.
- The status endpoint reports configuration and observed state without calling the API.
- Frontend: `CodeforcesSection` reuses the GitHub section's layout. The shared badge and date helpers live in `components/platform/StatusBadge.tsx`.

### LeetCode: manual self-reported evidence (Phase 4)

**Integration type: manual candidate-submitted evidence. Not official-API based, not authorised-integration based. Automated platform data: unavailable.**

Feasibility (2026-11-13): there is no official API, developer programme or OAuth. The [Terms of Service](https://leetcode.com/terms/) (last modified 04/05/2024) forbid crawling, scraping, spidering and reverse engineering, and [robots.txt](https://leetcode.com/robots.txt) disallows `/graphql`, `/api/` and `/submissions`. Third-party wrappers rely on the internal, undocumented GraphQL endpoint, often with session cookies. The findings are kept in `services/leetcode/feasibility.py` and served by `GET /integrations/leetcode/status`.

```
POST /candidates/{id}/leetcode -> validation.parse_leetcode_profile()  (stored only; never fetched or checked)
   -> developer_profiles row: collection_status=unavailable, sync_status=not_applicable, ownership=unverified
PUT  /candidates/{id}/leetcode/evidence -> LeetCodeEvidenceInput (strict, cross-field rules)
   -> evidence.build_evidence(): every section {status, note, provenance: self_reported / not_verified /
      manual_entry / reported_by / claimed_profile_url / submitted_at}; not provided -> null + not_collected
   -> self_reported_data, self_reported_at   (collected_data / collected_at stay NULL)
```

- **Structural separation (migration 0005):** self-reported evidence has its own columns, so aggregation in later phases cannot confuse it with platform-collected evidence. The stored schema accepts only `self_reported` / `not_verified`, and the frontend parser rejects anything else.
- **No network path:** LeetCode modules import no HTTP client, and tests enforce this plus the absence of any LeetCode endpoint, cookie or session string. API and live tests fail on any outbound request.
- **No sync or retry endpoints**, because no permitted source exists. Freshness comes from the "as of" date (stale after 30 days, configurable).

### Resume pipeline (Phase 1A)

```
POST /candidates/{id}/resumes
  │ validate (magic bytes + structure, size, pages, encryption, macros, zip bomb) ──► 4xx, nothing stored
  │ store file as storage/resumes/<uuid>.<ext> (atomic) + insert row (pending) ──► 202
  ▼ background task (ResumeProcessor)
  extract: PyMuPDF per page ─(scanned page?)─► Tesseract OCR via MuPDF │ python-docx (header, body, tables)
  │ save extracted_text, extraction_method, page_count
  ▼
  structure: LLM (Groq/OpenRouter, JSON mode) ─► json.loads ─► ParsedResume (Pydantic, extra=forbid)
  │                                   invalid ─► one corrective retry ─► llm_invalid_response
  ▼ grounding: drop email / phone / links / skills absent from the text (warnings)
  row: completed + parsed_data | failed + parse_error_code (text kept for re-parse)
```

The JD pipeline (Phase 1B) is the same shape: `services/job_description/{structuring,processor}.py` reuse validation (with TXT), extraction, OCR and storage from `services/resume/`. The shared `services/llm_json.py` loop handles call, JSON parse, Pydantic validation and corrective retry for both document types. JDs store two structured versions: `analysis`, the grounded LLM output (never edited), and `reviewed_data`, the optional human corrections, which are effective when present.

Module boundaries:
- `integrations/llm.py` is transport only.
- `services/resume/*` holds validation, extraction, storage, structuring and orchestration.
- `api/v1/resumes.py` is HTTP only.

Processing is an in-process FastAPI background task for now. A server restart mid-parse leaves the row `processing`, and the re-parse endpoint accepts such rows after 10 minutes. A durable queue is a later-phase item.

## 9. External data-source policy (applies to later phases)

1. Use official APIs or explicitly permitted sources only. No scraping against a
   platform's terms of service and no circumventing authentication or rate limits.
2. Store the source URL, collection timestamp and provenance with every collected fact.
3. Distinguish *unavailable* (null or unknown) from *zero*.
4. A missing or private profile is never evidence that a skill is missing.
5. Treat resumes, repository content and API responses as untrusted data, never as
   instructions, including when they are passed to an LLM.
6. Verify profile ownership where the platform makes it possible.

Initial assessment, to be re-checked against each platform's current terms in its own
phase:

| Platform | Expected source |
|---|---|
| GitHub | Official REST/GraphQL API: **implemented in Phase 2** (REST) |
| Codeforces | Official public API: **implemented in Phase 3** (`user.info`, `user.rating`, `user.status`; no key) |
| LeetCode | **No official or permitted API** (terms forbid scraping; robots.txt disallows `/graphql`, `/api/`). **Phase 4: manual self-reported evidence only**, never contacted |
| Kaggle | Official API (requires the user's API token) |
| HackerRank, CodeChef | No documented public profile API known. Options: skip, candidate-supplied evidence, or mark as "unavailable" (to be investigated in their own phases) |
