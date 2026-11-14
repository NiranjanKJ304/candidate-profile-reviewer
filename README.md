# Candidate Intelligence

AI-powered multi-platform candidate intelligence and recruitment evaluation system.

The system will compare a candidate's resume and public developer-platform evidence
(GitHub, LeetCode, HackerRank, Codeforces, CodeChef, Kaggle) against a job description,
and produce an evidence-based evaluation report: supported claims, discrepancies,
missing evidence, skill gaps and targeted interview questions.

> **Current status:**
> - Phase 0 (foundation) is complete.
> - Phases 1A (resume parsing) and 1B (JD parsing) are implemented but wait on a real Groq run.
> - Phase 2 (GitHub developer profiles) is complete and verified against the real GitHub API.
> - Phase 3 (Codeforces developer profiles) is complete and verified against the real Codeforces API.
> - Phase 4 (LeetCode) is complete as a **manual, self-reported evidence** workflow. LeetCode has no permitted API, so it is never contacted.
> - Phase 5 (candidate–JD evaluation) is complete: open `/evaluations` to see whether a candidate suits a job.
> - Other platforms, aggregation, scoring and reports are not started.
>
> **Docker is not used**: everything runs against natively installed services.
> See [PROJECT_STATUS.md](PROJECT_STATUS.md) and [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md).

## Repository layout

```
.
├── backend/              FastAPI service (Python 3.12, uv)
│   ├── app/              application package (see ARCHITECTURE.md)
│   ├── alembic/          database migrations (0001-0006)
│   └── tests/            unit/ and integration/ tests
├── frontend/             Next.js 16 + TypeScript + Tailwind CSS 4
├── scripts/              setup-local-db.ps1, integration-test.ps1
├── docker-compose.yml    OPTIONAL, unused and unverified (Docker not available)
├── .env.example          variables for the optional compose file only
└── *.md                  project documentation
```

## Prerequisites

| Tool | Version used | Notes |
|---|---|---|
| uv | 0.13.7 | Manages Python 3.12 and the backend virtualenv |
| Python | 3.12.16 (uv-managed) | Independent of any global Python install |
| Node.js / npm | 24.x / 11.x | Frontend |
| PostgreSQL | 18 (Windows service, port 5432) | Application database |
| Memurai | 8.2 (Windows service, 127.0.0.1:6379) | Windows-native server compatible with Redis 8.2; used as Redis |
| Tesseract OCR | 5.4.0, `eng` data | OCR for scanned PDFs (`winget install UB-Mannheim.TesseractOCR`); auto-detected |
| Groq API key | n/a | Resume structuring. Set `GROQ_API_KEY` in `backend/.env`; without it, resumes are extracted but not structured |

### Resume parsing (Phase 1A)

1. Add `GROQ_API_KEY=<your key>` to `backend/.env` (see `backend/.env.example` for every LLM, resume and OCR setting).
2. `cd backend; uv run alembic upgrade head` (migration `0002_resume_parsing`).
3. Start the backend and frontend, then open http://localhost:3000/resumes.

Original files are stored in `backend/storage/resumes/` (git-ignored). Endpoints are documented in [API_DOCUMENTATION.md](API_DOCUMENTATION.md#resumes).

### Job-description parsing (Phase 1B)

1. Same Groq key and OCR setup as resume parsing; JD limits are `JOB_DESCRIPTION_*` in `backend/.env.example`.
2. `cd backend; uv run alembic upgrade head` (migration `0003_job_description_parsing`).
3. Open http://localhost:3000/job-descriptions. You can upload PDF, DOCX or TXT, review required vs preferred skills, experience, education and responsibilities (each with its source quote), and **correct the extraction** before evaluation. The original extraction is kept, and you can restore it.

Original JD files are stored in `backend/storage/job_descriptions/` (git-ignored). Endpoints: [API_DOCUMENTATION.md](API_DOCUMENTATION.md#job-descriptions).

### GitHub developer profiles (Phase 2)

1. In `backend/.env` set `ENABLED_PLATFORMS=github`. A `GITHUB_TOKEN` is optional and needs no scopes; it raises the limit from 60 to 5,000 requests/hour. Other `GITHUB_*` limits are in `backend/.env.example`.
2. `cd backend; uv run alembic upgrade head` (migration `0004_developer_profile_sync`).
3. Open http://localhost:3000/candidates, add or select a candidate, and connect a GitHub username or `https://github.com/{username}` URL. The same section appears under an uploaded resume.

Only public data from the official REST API is collected: profile, repositories, languages and topics, README excerpts, a recent public-activity summary, and authored PR/issue counts. No e-mail addresses, commit messages or private repositories are stored. Each data category records its availability and source. Ownership is never assumed. Endpoints: [API_DOCUMENTATION.md](API_DOCUMENTATION.md#github-developer-profiles-phase-2).

Real GitHub API tests (network, opt-in): `powershell -ExecutionPolicy Bypass -File scripts\integration-test.ps1 -GitHubLive`, or `INTEGRATION_GITHUB_LIVE=1 uv run pytest -m integration tests/integration/test_github_real_api.py`.

### Codeforces developer profiles (Phase 3)

1. In `backend/.env` add `codeforces` to `ENABLED_PLATFORMS` (e.g. `ENABLED_PLATFORMS=github,codeforces`). No API key is needed. Limits are `CODEFORCES_*` in `backend/.env.example`: the submission window defaults to 500 (maximum 1000), and requests are paced at 1 per 2 s or slower.
2. No migration is needed (still `0004`).
3. On http://localhost:3000/candidates (or under an uploaded resume), connect a Codeforces handle or `https://codeforces.com/profile/{handle}` URL.

Only the official public API is used (`user.info`, `user.rating`, `user.status`). The section shows rating and rank, contest history, recent submissions, and statistics for solved unique problems, difficulty, topics, languages and verdicts. Lifetime values and collected-window values are labelled separately. No source code, e-mail or team-mate handles are stored. Endpoints: [API_DOCUMENTATION.md](API_DOCUMENTATION.md#codeforces-developer-profiles-phase-3).

Real Codeforces API tests (network, opt-in): `powershell -ExecutionPolicy Bypass -File scripts\integration-test.ps1 -CodeforcesLive`, or `INTEGRATION_CODEFORCES_LIVE=1 uv run pytest -m integration tests/integration/test_codeforces_real_api.py`.

### LeetCode: self-reported evidence (Phase 4)

LeetCode publishes no official API, and its [Terms of Service](https://leetcode.com/terms/) forbid scraping (its [robots.txt](https://leetcode.com/robots.txt) also disallows `/graphql` and `/api/`). **This system therefore never contacts LeetCode.** Instead:

1. Add `leetcode` to `ENABLED_PLATFORMS` (e.g. `ENABLED_PLATFORMS=github,codeforces,leetcode`) and run `uv run alembic upgrade head` (migration `0005_self_reported_evidence`).
2. On http://localhost:3000/candidates, link a LeetCode username or `https://leetcode.com/u/{username}/` URL. It is stored, never fetched or checked.
3. Use **Add self-reported evidence** to enter what the candidate reports: solved counts by difficulty, contest rating and history, languages, topics. Empty fields mean "not provided", never zero.

Everything is labelled **self-reported and not verified** in the API, the database (stored apart from platform-collected evidence) and the UI. There is no sync. Endpoints: [API_DOCUMENTATION.md](API_DOCUMENTATION.md#leetcode-profiles-phase-4-manual-self-reported-evidence-only).

### Evaluate a candidate against a job description (Phase 5)

1. Parse a resume (`/resumes`) and a JD (`/job-descriptions`). Optionally link GitHub, Codeforces and LeetCode on `/candidates`.
2. `cd backend; uv run alembic upgrade head` (migration `0006_evaluation_errors`).
3. Open http://localhost:3000/evaluations (**Evaluate** in the navigation), choose the candidate and the JD, and click **Evaluate**.

You get a 0–100 score and fit label, a verdict per requirement (met / partially met / not met / not enough evidence) with the evidence behind it, unproven required items, strengths, gaps and discrepancies. The LLM (`openai/gpt-oss-120b` on Groq) gives the verdicts; the backend computes the score and enforces the rules: no verdict without evidence, and missing evidence never counts as "not met". Personal details are removed before anything is sent to the LLM. The result supports a recruiter's decision; it does not make one. Endpoints: [API_DOCUMENTATION.md](API_DOCUMENTATION.md#evaluations-phase-5-is-a-candidate-suitable-for-a-job-description).

### Real-provider LLM tests

When `GROQ_API_KEY` is set in `backend/.env`, `scripts\integration-test.ps1` also runs `tests/integration/test_llm_provider_live.py` against Groq. These tests cost tokens; without a key they are skipped.

### One-time setup: Python 3.12 with uv (Windows PowerShell)

```powershell
winget install --id=astral-sh.uv -e      # or: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# open a new terminal so PATH is refreshed
uv --version
uv python install 3.12
```

## Running locally (no Docker)

The backend and frontend run independently against the native PostgreSQL and Memurai
services.

### One-time database setup

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-local-db.ps1
```

This script:
- creates `backend/.env` from `backend/.env.example`;
- generates a random password for the application role and saves it there (it is never printed);
- creates a least-privilege `candidate_app` role and a `candidate_intel` database. psql prompts
  for your PostgreSQL superuser (`postgres`) password; the script never stores it;
- verifies that the new credentials work.

It is safe to re-run. Memurai needs no setup: by default it listens only on `127.0.0.1`.

> Use `127.0.0.1`, not `localhost`, in `DATABASE_URL` and `REDIS_URL`. On Windows,
> `localhost` tries IPv6 `::1` first. Memurai listens on IPv4 only, so that adds about
> 2 seconds to every new Redis connection, enough to time out the readiness check.

### Backend

```powershell
cd backend
uv sync                                  # creates backend/.venv with Python 3.12
uv run alembic upgrade head              # applies migrations 0001-0006
uv run uvicorn app.main:create_app --factory --reload --port 8000
```

- API docs (non-production only): http://localhost:8000/docs
- Liveness: http://localhost:8000/api/v1/health
- Readiness: http://localhost:8000/api/v1/ready

The backend starts even when PostgreSQL or Redis are down. `/ready` then returns `503`
with per-dependency status. If required configuration is missing or invalid, startup
fails immediately with a validation error.

### Database migrations (Alembic)

```powershell
cd backend
uv run alembic upgrade head      # apply all migrations
uv run alembic current           # show the applied revision (expect 0006_evaluation_errors)
uv run alembic check             # fails if the models and the database differ
uv run alembic downgrade base    # drop everything (development only)
uv run alembic upgrade head --sql   # print the SQL without a database
```

### Data model

Five tables, all with UUID primary keys and `created_at`/`updated_at` timestamps:

| Table | Purpose | Relationships |
|---|---|---|
| `candidates` | The person being evaluated | parent of resumes, developer profiles and evaluations |
| `resumes` | Uploaded resume metadata and parse status | belongs to a candidate (deleted with it) |
| `job_descriptions` | The role being evaluated against | cannot be deleted while evaluations reference it |
| `developer_profiles` | One account per platform per candidate, with ownership and collection status | belongs to a candidate (deleted with it) |
| `evaluations` | One assessment of a candidate against a job description | candidate (cascade), job description (restrict), resume (set to null on delete) |

Deleting a candidate removes all of their personal data. Details are in
[ARCHITECTURE.md](ARCHITECTURE.md#5-data-layer).

### Frontend

```powershell
cd frontend
npm ci
Copy-Item .env.example .env.local        # NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
npm run dev                              # http://localhost:3000
```

## Quality checks and tests

```powershell
# Backend
cd backend
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest --cov                      # 1,073 unit tests (no services or network needed)

# Backend live tests: 129 tests (services, migrations, resume/JD/platform/evaluation workflows;
# real LLM/GitHub/Codeforces tests are opt-in: add -GitHubLive and/or -CodeforcesLive).
# Reads backend/.env. Migrations run in a random temporary schema that is dropped afterwards,
# so your existing tables are never touched.
cd ..
powershell -ExecutionPolicy Bypass -File scripts\integration-test.ps1

# Frontend
cd frontend
npm run lint
npm run typecheck
npm test
npm run build
```

Latest recorded results are in [TEST_REPORT.md](TEST_REPORT.md).

## Security notes

- Secrets live only in git-ignored `.env` files. The `*.env.example` files hold placeholders.
  With `ENVIRONMENT=production`, the backend refuses to start if a placeholder password is used.
- Uploaded resumes and candidate data must never be committed. `.gitignore` blocks
  `uploads/`, `resumes/`, `data/`, `*.pdf`, `*.docx` and similar paths.
- Logs are structured JSON with automatic secret redaction.
- The application connects as `candidate_app`, which is not a superuser and cannot create
  databases or roles. A live test checks this.

## Docker (optional, unused)

`docker-compose.yml` and the two `Dockerfile`s are kept as a starting point for later, but
Docker is not available on the development machine. They have **never been built or run**
and are not part of the supported workflow.

## Documentation

- [PROJECT_STATUS.md](PROJECT_STATUS.md): current status, completion criteria, open items
- [ARCHITECTURE.md](ARCHITECTURE.md): design, module boundaries, phase roadmap, data-source policy
- [API_DOCUMENTATION.md](API_DOCUMENTATION.md): HTTP endpoints and error format
- [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md): what was built in each phase, decisions and known issues
- [TEST_REPORT.md](TEST_REPORT.md): actual test results
