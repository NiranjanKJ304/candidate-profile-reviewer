# Candidate Intelligence

AI-powered multi-platform candidate intelligence and recruitment evaluation system.

The system will compare a candidate's resume and public developer-platform evidence
(GitHub, LeetCode, HackerRank, Codeforces, CodeChef, Kaggle) against a job description,
and produce an evidence-based evaluation report: supported claims, discrepancies,
missing evidence, skill gaps and targeted interview questions.

> **Current status: Phase 0 (foundation).** Only the project skeleton exists: configuration,
> logging, health/readiness endpoints, database and cache wiring, a minimal frontend and
> tests. **Docker is not used**: everything runs against natively installed services.
> No resume parsing, platform collectors, LLM calls or scoring are implemented yet.
> See [PROJECT_STATUS.md](PROJECT_STATUS.md) and [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md).

## Repository layout

```
.
├── backend/              FastAPI service (Python 3.12, uv)
│   ├── app/              application package (see ARCHITECTURE.md)
│   ├── alembic/          database migrations (none yet)
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
uv run alembic upgrade head              # creates the 5 domain tables (migration 0001)
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
uv run alembic current           # show the applied revision (expect 0001_initial_schema)
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
uv run pytest --cov                      # 484 unit tests (no services needed)

# Backend live tests: 72 tests (services, migrations, models, resume + JD workflows, real LLM if keyed).
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
