# Development Log

## Phase 1B: Job Description Parsing Engine · 2026-11-12

**Status:** Implemented and tested; **not complete.** The JD upload-to-database workflow works live (TXT, PDF, scanned PDF via OCR, DOCX) up to the LLM call. The real Groq structuring call has not run, because `GROQ_API_KEY` is still not configured. Phase 1A is blocked on the same step.

**Inspected first:** PROJECT_STATUS.md, ARCHITECTURE.md and the resume engine.
- The existing `JobDescription` model required `title` and `description_text` (NOT NULL), which an uploaded file cannot provide before parsing.
- It had no file or provenance columns.
- So **one migration (`0003`) was necessary**; everything else is reused.

**Reused (extended, not rewritten)**
- `validate_upload` gained `allowed=` (default unchanged: PDF/DOCX for resumes) and TXT support:
  - decoding in the order UTF-8, UTF-8 with BOM, UTF-16 with BOM, then cp1252;
  - rejection of NUL bytes or more than 5% control characters (binary data), and of whitespace-only files;
  - rejection of a PDF or ZIP renamed to `.txt`.
- `extract_text` dispatches `txt` → new `extract_txt`; `ExtractionMethod.TXT` added.
- `ResumeStorage` takes an `extensions` tuple (per-instance key regex); the JD store allows `txt`.
- **Refactor:** the resume structurer's JSON-retry loop moved into `app/services/llm_json.py` (`complete_validated`, `prepare_document_text`, `require_configured`, `parse_llm_json`, `schema_json`). `ResumeStructurer` now calls it. All 38 resume structuring and processor tests passed unchanged before any JD code relied on it.
- The LLM client, OCR configuration, error codes, processor pattern, API conventions and frontend API client are all reused.

**New**
- `app/schemas/job_description_parsing.py`:
  - strict `ParsedJobDescription`, with `evidence` on every requirement;
  - separate `required_skills` and `preferred_skills`;
  - experience range validation;
  - `TechnologySummary` derived from skill categories (languages, frameworks/libraries, databases, cloud/DevOps, tools, other).
- `app/services/job_description/structuring.py`: dedicated prompt (never infer, mandatory vs optional, verbatim evidence, untrusted-data delimiters), plus JD grounding. Grounding:
  - drops unsupported skills, texts, education and experience;
  - requires years to appear in their quote;
  - clears unstated seniority;
  - keeps a skill in both lists as required only;
  - normalises typographic quotes and dashes.
- `app/services/job_description/processor.py`: background pipeline. It sets `title`, `company` and `description_text` from real data only, and never overwrites `reviewed_data`.
- Migration **`0003_job_description_parsing`**:
  - `title` and `description_text` become nullable, with NULL-tolerant not-blank checks;
  - 16 columns are added (file reference, extracted text, status, error code, warnings, model, `parsed_at`, `reviewed_data`, `reviewed_at`);
  - a unique `sha256` constraint;
  - extraction-method checks gain `txt` on both tables.

  It was applied to your database, and `alembic check` reports no drift.
- API `/api/v1/job-descriptions`: `POST /upload`, `GET` (paginated), `GET /{id}`, `GET /{id}/parsed`, `PUT /{id}/parsed` (review), `DELETE /{id}/parsed/review`, `POST /{id}/parse`, `GET /{id}/file`, `DELETE /{id}` (409 when evaluations reference it). The capabilities endpoint reports JD limits.
- Settings: `JOB_DESCRIPTION_STORAGE_DIR`, `JOB_DESCRIPTION_MAX_BYTES` (5 MB), `JOB_DESCRIPTION_MAX_PAGES` (20).
- Frontend:
  - `lib/parse.ts` (validation helpers), `lib/jobDescriptions.ts` (types, parsers, calls, review line-editing helpers);
  - `apiClient` gained `PUT`;
  - `/job-descriptions` page with `JobDescriptionWorkspace`, `JDView` (evidence quotes, required vs preferred, technology summary) and `JDReviewEditor` (corrections; edited values drop their evidence quote);
  - a navigation link; the landing page marks JD analysis "Available".
- `scripts/integration-test.ps1` passes `GROQ_API_KEY` from `backend/.env` to the new real-provider tests (`test_llm_provider_live.py`) when set.

**Defects found and fixed**
1. **Escape mangling again.** My inline Python edit of `validation.py` wrote raw NUL, tab, newline, form-feed and BOM bytes into the source. `grep` reported a binary file; a byte-level `repr` scan confirmed it. The block was rewritten with `chr()`/`bytes([...])` through a script file, and the result was verified as 0 NUL bytes and 0 control characters.
2. **Unchecked cast in my own frontend code.** I first read JD limits with `api.get(..., v => v as ...)`, bypassing validation. The existing capabilities parser now validates the optional `job_description` section, and the cast is gone.
3. **Converted escapes.** The `’`-style escapes in the JD grounding normaliser were converted to literal characters by my tooling; they are now a `str.maketrans` table built from `chr()` code points.
4. A frontend test scripted three list responses where only two calls happen. This was a test bug.

**Not done (by instruction):** GitHub, LeetCode, candidate matching and scoring.

---

## Phase 1A: Resume Parsing Engine · 2026-11-12

**Status:** Implemented and tested; **not complete.** The real upload-to-database workflow works live up to the LLM call. The Groq structuring call has not run against the real API, because `GROQ_API_KEY` is not configured. Per the phase rules, it is not marked complete until that run succeeds (see PROJECT_STATUS.md, "To finish Phase 1A").

**Decisions (asked first):**
- OCR engine: installed **Tesseract 5.4.0** with winget (UB-Mannheim build, per-user install at `%LOCALAPPDATA%\Programs\Tesseract-OCR`, `eng` data).
- LLM provider: **Groq**. OpenRouter is also supported, selected with `LLM_PROVIDER`.

**Built (reusing the Phase 0 architecture)**
- Dependencies: `pymupdf` 2.1, `python-docx` 1.2, `python-multipart`.
- `app/core/config.py`:
  - LLM settings (provider, keys as `SecretStr`, model, https-only base URL, timeout, attempts, input cap);
  - resume and OCR settings (storage dir, 10 MB / 30-page limits, OCR language and dpi).
- `app/core/enums.py`: `ExtractionMethod`, `ResumeErrorCode`.
- Migration **`0002_resume_parsing`**: 6 nullable columns on `resumes` (extraction method, page count, extracted text, parser model, error code, warnings) plus 3 CHECK constraints. Applied to your database; `alembic check` reports no drift.
- `app/schemas/resume_parsing.py`: strict `ParsedResume` schema (unknown keys rejected, bounded sizes, placeholder strings become null).
- `app/integrations/llm.py`: OpenAI-compatible chat client with JSON mode, typed errors (not configured / auth / timeout / rate limit / response), one transport retry for 429/5xx, and no prompt or response content in logs.
- `app/services/resume/`:
  - `validation.py`: magic-byte and structure checks; extension must match content; rejects empty, corrupted, truncated (repaired without `%%EOF`), encrypted, too many pages, macro-enabled, zip bomb and plain ZIP; sanitises filenames.
  - `extraction.py`: PyMuPDF per page; scanned-page detection (fewer than 30 characters, or more than 30% U+FFFD) triggers Tesseract OCR via MuPDF; DOCX headers, body and tables in order, plus footers; text normalisation.
  - `storage.py`: UUID-named files, atomic writes, strict key validation (no traversal).
  - `structuring.py`: delimited untrusted-text prompt, JSON parse and Pydantic validation, one corrective retry, and a grounding pass that removes emails, phones, links and skills not present in the text.
  - `processor.py`: background pipeline that records every outcome on the row and keeps the extracted text when the LLM fails.
- API:
  - `POST/GET /candidates`, `GET /candidates/{id}/resumes`;
  - `POST /candidates/{id}/resumes` (202), `GET /resumes/{id}`, `GET /resumes/{id}/file`, `POST /resumes/{id}/parse`, `DELETE /resumes/{id}`;
  - `GET /system/capabilities`;
  - database outages map to 503 `database_unavailable`.
- Frontend:
  - the shared API client gained POST, DELETE, FormData, 204 handling and error codes;
  - `lib/resumes.ts` adds runtime-validated types and calls;
  - `/resumes` page with `ResumeWorkspace` (capability notices, client checks, polling, retry, delete, download) and `ParsedResumeView` ("not found" for missing sections);
  - header link added; the landing page marks resume analysis "Available".

**Defects found and fixed during this phase**
1. **Truncated PDFs were accepted.** MuPDF silently repairs them and returns partial text. Repaired PDFs without `%%EOF` are now rejected.
2. **`ResumeStorage.save` raised a raw `OSError`** when the directory could not be created, instead of `StorageError`.
3. **I first registered `OSError` as a "database unavailable" error.** That would have mislabelled file errors, so it was removed before any test depended on it.
4. **The production build failed.** The server page passed a function-bearing API client object to a client component. It now passes the base URL string instead.
5. **Accessibility.** The file input's label contained the hint text, so its accessible name was a paragraph. The hint is now linked with `aria-describedby`.
6. **Escape-sequence mangling.** `\u` escapes written by my tooling became literal invisible characters (NBSP, U+FFFD, a combining accent). One shell edit also broke `normalize_text` with raw CR characters. All were repaired and replaced with `chr()` constants, verified by a non-ASCII scan, and covered by a test.
7. **Coverage under-reported async code.** SQLAlchemy's greenlets hid executed lines, so coverage now uses `concurrency = ["greenlet", "thread"]`.

**Live verification on your machine:** a real backend, your PostgreSQL and Tesseract, with synthetic files.
- Text PDF, scanned PDF (via OCR) and DOCX were all accepted, extracted and stored.
- Each ended `failed` / `llm_not_configured` as designed.
- Rejection (422), duplicate (409), download (byte-identical) and delete (row and file removed) all behaved correctly.
- 0 occurrences of resume content in the logs.

Two synthetic resumes are kept for the final Groq verification.

**Not done (by instruction):** JD parsing, GitHub or other platform integrations. No mock data is used in application workflows; the LLM test double exists only under `tests/`.

---

## Phase 0, revision 2: domain models, migrations, layout · 2026-11-12

**Request:** the full Phase 0 specification, adding initial models (Candidate, Resume,
JobDescription, DeveloperProfile, Evaluation) with UUID keys, Alembic migrations, a
frontend API client, an app layout and a candidate evaluation landing page.

**Inspected first:**
- No commits exist yet.
- Every file changed since revision 1 was from that session's own formatter runs.
- Two new files: a root `.env` (yours, git-ignored; `REDIS_PASSWORD` is empty) and
  `frontend/next-env.d.ts` (regenerated by `npm run dev`).
- `backend/.env` does not exist yet, so `setup-local-db.ps1` had not been run.

**Built**
- `app/core/enums.py` holds the domain enums. `Platform` moved here from `config.py`,
  which re-exports it.
- `app/db/base.py`:
  - `UUIDPrimaryKeyMixin` (application `uuid4` plus `gen_random_uuid()` fallback);
  - a `JsonDocument` type (JSONB on PostgreSQL);
  - `string_enum()` (VARCHAR plus CHECK);
  - the composite-unique naming convention.
- `app/models/`: the five models, with relationships and delete rules (see ARCHITECTURE.md §5).
- `alembic/versions/20261112_0001_initial_schema.py`, a hand-written migration whose
  constraint names match the naming convention. `env.py` now imports the models and
  accepts a test connection.
- `app/schemas/domain.py`: Create and Read schemas.
- Frontend:
  - `src/lib/apiClient.ts`, a shared client with versioned URLs and `createApiClient`;
  - `status.ts` now builds on it;
  - `SiteHeader` and `SiteFooter` added, with a skip link in the layout;
  - "How an evaluation works" section on the landing page.
- `docker-compose.yml`: added a one-shot `migrate` service that the backend waits for.
  The file has still **not been run**.

**Verification without Docker or your credentials:** I created a throwaway PostgreSQL 18
instance with `initdb` in the session's scratch folder (port 55432, random passwords), then:
- ran `setup-local-db.ps1` end to end against it, twice;
- ran `alembic upgrade head` and `alembic check`;
- ran all 29 live tests;
- served the backend and frontend live.

Afterwards I stopped and deleted the instance. Your PostgreSQL service on 5432 was not used
and is confirmed still running.

**Fixed during this revision**
- A blank e-mail normalised to `None` then failed the `max_length` constraint. Found by a
  schema test.
- The `test_db.py` throwaway tables were registered on the application metadata. They would
  have shown up as false migration drift.

**Not done (by instruction):** Docker Compose was not started, because Docker is not to be used.

---

## Phase 0, revision 1: Docker removed from the workflow · 2026-11-11

**Decision (user):** Docker does not work on the development machine, so Docker is not to
be used.

**Native services found already installed and running as Windows services:**
- PostgreSQL 18 on 5432 (password authentication);
- Memurai 8.2 (compatible with Redis 8.2) on 127.0.0.1:6379, no password, protected mode on.

**Changes**
- `backend/.env.example` now targets native services: `127.0.0.1:5432` for PostgreSQL and
  `redis://127.0.0.1:6379/0` for Memurai.
- New `scripts/setup-local-db.ps1`, an idempotent one-time setup:
  - creates `backend/.env` and generates a random password for the app role (never printed);
  - creates the `candidate_app` role (LOGIN, NOSUPERUSER, NOCREATEDB, NOCREATEROLE) and the
    `candidate_intel` database owned by it, with PUBLIC access revoked;
  - verifies the credentials.

  The superuser password is typed into psql's own prompt; the script never handles it.
- `scripts/integration-test.ps1` was rewritten without Docker. It reads `backend/.env`,
  checks TCP reachability, then runs `pytest -m integration`.
- Live tests split so PostgreSQL and Redis can be verified independently. Added a wrong
  PostgreSQL password test and an assertion that the app role is not a superuser.
- Health probes now report the innermost driver error (`InvalidPasswordError` instead of
  SQLAlchemy's generic `DBAPIError`).
- The Docker files are kept but labelled "OPTIONAL - NOT USED - NOT VERIFIED". They were
  not deleted because they are untracked and deletion could not be undone.

**Findings**
- On Windows, `localhost` → `::1` first, and Memurai listens on IPv4 only, so every new
  Redis connection took about 2 s and readiness timed out. All URLs now use `127.0.0.1`.
  Measured: 2056 ms against 2 ms.
- See TEST_REPORT.md, revision 1, for the results and four defects fixed.

**Status:** Phase 0 checks pass, except the 3 live PostgreSQL tests. Those need you to run
`scripts/setup-local-db.ps1` once, because it requires your PostgreSQL superuser password.

---

## Phase 0: Foundation · 2026-11-11

**Status:** Implemented. All local checks pass. ~~The Docker-dependent checks have not run
yet~~: superseded by revision 1 above, since Docker is no longer used.

### Starting point

The working directory was empty and not a git repository. The machine had Python 3.11.9
only, Node 24.19, Docker CLI 29.8 (engine stopped) and a local PostgreSQL 18 on port 5432.

### Approved decisions

| # | Decision |
|---|---|
| 1 | Python 3.12+ via **uv**, isolated from the global Python 3.11 (`uv python install 3.12`, project `.venv`) |
| 2 | The user starts Docker Desktop before the Docker integration tests |
| 3 | Minimal Next.js + TypeScript + Tailwind frontend: landing page plus backend health/readiness status |
| 4 | Git initialised; comprehensive `.gitignore`; never commit `.env`, credentials, tokens, resumes or candidate PII |
| 5 | PostgreSQL in Docker on host port **5433** |
| 6 | Redis in Docker |
| 7 | Backend and frontend each runnable without Docker |
| 8 | FastAPI async, SQLAlchemy 2 async, asyncpg, Alembic |
| 9 | Health and readiness endpoints with separate PostgreSQL and Redis checks |
| 10 | Platform integrations modular and configurable; **no collectors in Phase 0** |

### What was built

**Backend** (`backend/`)
- `pyproject.toml` with locked dependencies (`uv.lock`) and `.python-version` pinned to 3.12.
- Typed settings with `SecretStr` credentials, fail-fast validation and production guards.
- Structured JSON logging with layered secret redaction (message, args, `extra`, tracebacks).
- Pure-ASGI middleware for request IDs, the access log and security headers. CORS uses an allow-list.
- Standard error envelope. Validation errors never echo input. 500s are generic and still
  keep the request ID, CORS and security headers.
- `/api/v1/health` (liveness) and `/api/v1/ready` (concurrent, timeout-bounded
  PostgreSQL/Redis probes with sanitised errors).
- SQLAlchemy async engine/session factories, a declarative `Base` with a naming convention,
  and `TimestampMixin`.
- Async Alembic environment that reads the URL from settings. No migrations yet.
- `Platform` enum and the `ENABLED_PLATFORMS` setting. `app/integrations/` is a documented,
  empty package.
- A multi-stage Dockerfile built with uv: non-root uid 10001, root-owned code, healthcheck.

**Frontend** (`frontend/`)
- Next.js 16.4.6 / React 19.3 / Tailwind 4.3 / TypeScript 5.9, scaffolded with
  `create-next-app` and adapted:
  - Google Fonts removed, so builds need no network font fetch;
  - security headers added; `poweredByHeader` off; standalone output.
- Landing page with "Planned" capability cards, clearly marked as not active.
- `StatusPanel` with a validated API client that keeps unreachable, invalid and ok apart.
  When the backend is unreachable, dependencies show as *Unknown*.
- Vitest + Testing Library setup.
- A multi-stage Dockerfile running as the non-root `node` user, with a healthcheck.

**Infrastructure and docs**
- `docker-compose.yml`:
  - postgres:17-alpine, redis:7.4-alpine, backend and frontend;
  - health checks with health-gated startup;
  - 127.0.0.1-only ports;
  - `no-new-privileges`, `cap_drop: ALL` with minimal additions, read-only root
    filesystems, tmpfs scratch, log rotation;
  - required secrets enforced with `${VAR:?}`.
- `.env.example` (Compose), `backend/.env.example`, `frontend/.env.example`.
- `scripts/integration-test.ps1` for the live tests.
- README, ARCHITECTURE, API_DOCUMENTATION, TEST_REPORT and this log.

### Notable decisions and rationale

- **App factory with `--factory`:** settings load at startup rather than import, so tests
  build apps with explicit settings and need no environment hacks.
- **Lazy connections:** the API starts while dependencies are down and reports this through
  `/ready`. That suits container orchestration and makes the failure visible.
- **Probe errors expose only the exception type:** driver messages can contain hostnames
  and usernames.
- **`UnhandledExceptionMiddleware`:** Starlette's `Exception` handler runs outside the user
  middleware. Without this middleware, 500 responses lacked the request ID, CORS and
  security headers. A test found this before release.
- **Unknown is not down:** when the frontend cannot reach the backend, it shows dependency
  status as *Unknown* rather than failed. This follows the project rule that unavailable
  information is not a zero or negative value.
- **No CSP yet:** a strict Content-Security-Policy needs nonce plumbing with Next.js
  inline scripts. It is deferred until the frontend has real features.
- **Integration tests skip only when not configured:** if the URLs are set but the services
  are down, the tests *fail*, so a broken environment cannot pass silently.

### Known issues and follow-ups

1. Docker image builds, container runtime hardening and the 4 live integration tests are
   still to run. Steps are in TEST_REPORT.md.
2. Starlette `TestClient` deprecation warning (`httpx` → `httpx2`). It affects tests only.
   Installing `httpx2` was blocked by the session's permission policy.
3. 7 moderate `npm audit` findings in dev-only ESLint dependencies (`minimatch`). Production
   dependencies have none.
4. The frontend `package-lock.json` was generated on Windows. The Docker (Linux/musl) build
   must confirm that npm resolves the platform-specific optional native packages.
5. Nothing has been committed yet. The repository is initialised and `.gitignore` has been
   verified; the first commit is left to the user.
6. In the frontend, `next dev` may re-add its own `AGENTS.md` rules block. Committing that
   file keeps the working tree clean.
