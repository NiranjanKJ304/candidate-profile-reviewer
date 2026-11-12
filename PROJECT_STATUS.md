# Project Status

**Project:** AI-Powered Multi-Platform Candidate Intelligence and Recruitment Evaluation System
**Last updated:** 2026-11-12

| Phase | Status |
|---|---|
| Phase 0: Foundation | ✅ Complete and verified |
| Phase 1A: Resume Parsing Engine | 🟡 **Implemented, not complete.** Works live up to the LLM call; the real Groq call has not run (no `GROQ_API_KEY`) |
| **Phase 1B: Job Description Parsing Engine** | 🟡 **Implemented, not complete.** Upload, extraction and storage work live (TXT, PDF, scanned PDF via OCR, DOCX); the real Groq structuring call has **not run**, because no `GROQ_API_KEY` is configured |
| Phase 2+ (GitHub/LeetCode collectors, matching, scoring) | ⚪ Not started |

Both 1A and 1B are blocked on the **same single step**: adding a Groq API key and running one real parse (instructions below).

---

## At a glance

| Area | Status |
|---|---|
| Backend (FastAPI, Python 3.12) | ✅ Health, capabilities, candidate, resume and **job-description** APIs |
| Database (PostgreSQL 18, local) | ✅ `candidate_intel` at **`0003_job_description_parsing` (head)**, no drift (`alembic check`) |
| Redis (Memurai 8.2) | ✅ Connected |
| OCR (Tesseract 5.4.0) | ✅ Scanned resumes and JDs verified live |
| LLM (Groq) | 🔴 **Not configured**: `GROQ_API_KEY` is empty in `backend/.env` |
| Frontend (Next.js 16) | ✅ `/resumes` and **`/job-descriptions`** (upload, status, required vs preferred skills, review and correct) |
| Automated tests | ✅ **678 passing**: backend 484 unit + 70 live (2 real-provider tests skipped, no key), frontend 124 |
| Docker | ⚪ Not used (by decision) |

---

## Phase 1B: verified live on your machine (2026-11-12)

Real backend on `backend/.env`, your PostgreSQL, Tesseract; synthetic JDs ("Example Analytics Ltd"):

| Step | Result |
|---|---|
| Upload TXT / PDF / scanned PDF / DOCX (`POST /job-descriptions/upload`) | ✅ HTTP 202 each |
| Extraction | ✅ `txt` 600 chars · `pdf_text` 614 · **`pdf_ocr` 281** (image-only PDF) · `docx` 610 |
| **LLM structuring** | 🔴 Not verified; each correctly ended `failed` / `llm_not_configured`, with text kept for re-parse and **no invented title** |
| `GET /{id}/parsed` before success | ✅ 409 `parse_not_completed` |
| Duplicate upload | ✅ 409 `duplicate_job_description` with the existing id |
| Password-protected PDF / binary file renamed `.txt` | ✅ 422 `encrypted_file` / 415 `content_mismatch` |
| Download original | ✅ Byte-identical, attachment |
| Delete | ✅ 204, row and file removed, then 404 |
| JD text in logs | ✅ 0 occurrences |
| Frontend `/job-descriptions`; CORS preflight for review `PUT` | ✅ 200 / allowed |

Structuring, grounding, review and re-parse are covered by automated tests with a scripted LLM double (22 live workflow tests against PostgreSQL). Two real-provider tests (`test_llm_provider_live.py`, one resume and one JD) are ready and run automatically once a key is set.

### To finish Phases 1A and 1B

1. Add `GROQ_API_KEY=<your key>` to `backend/.env` (never commit it or paste it in chat), then restart the backend.
2. Run the real-provider tests and all live tests: `powershell -ExecutionPolicy Bypass -File scripts\integration-test.ps1` (expect 72 passed, 0 skipped).
3. Re-parse the synthetic records kept for this purpose:
   ```powershell
   # Job descriptions (TXT, scanned PDF)
   curl.exe -X POST http://127.0.0.1:8000/api/v1/job-descriptions/0c2cf97f-7bb9-4d42-843f-5dd3fd10d20e/parse
   curl.exe -X POST http://127.0.0.1:8000/api/v1/job-descriptions/83225881-fb38-49b5-a1db-6df1f221acf4/parse
   curl.exe http://127.0.0.1:8000/api/v1/job-descriptions/0c2cf97f-7bb9-4d42-843f-5dd3fd10d20e/parsed
   # Resumes (from Phase 1A)
   curl.exe -X POST http://127.0.0.1:8000/api/v1/resumes/395dcf20-b0ab-4729-802e-245c7cea9c7e/parse
   ```
   Expect `completed`, with required skills Python, FastAPI and PostgreSQL, preferred skills Docker, Kubernetes and AWS, and a 5-year minimum. If the default model has been retired, set `LLM_MODEL`.
4. Delete the synthetic data afterwards (`DELETE /job-descriptions/{id}`, `DELETE /resumes/{id}`).

---

## Phase 1B scope delivered

| Requirement | Delivered |
|---|---|
| 1. JD upload (PDF, DOCX, TXT) | `POST /api/v1/job-descriptions/upload`. TXT accepts UTF-8 (with or without BOM), UTF-16 (BOM) and cp1252; binary data renamed `.txt` is rejected. 5 MB / 20-page limits. Corrupted, empty, truncated, password-protected, macro-enabled and zip-bomb files are rejected. Scanned PDFs are OCR'd. |
| 2. Information extraction | Title, company, location, summary, required skills, preferred skills, minimum and preferred experience, education, responsibilities, qualifications, soft skills, seniority. Languages, frameworks/libraries, databases and cloud/DevOps are a **technology summary derived from skill categories**, so they can never disagree with the skill lists. |
| 3. LLM integration | Reuses the Groq/OpenRouter client. Dedicated JD prompt. JSON mode, strict Pydantic schema, corrective retry (shared `llm_json` loop, also used by resumes). Each requirement carries **verbatim JD evidence**. Model name and warnings recorded. Timeout and API failures map to error codes. |
| Never invent requirements | Grounding drops any skill, responsibility, qualification, education or experience item whose name or quote is not in the JD; years must appear in their quote; unstated seniority is cleared; a skill in both lists is kept as required only. |
| 4. Database | Reused the `job_descriptions` table. Migration `0003` adds the file reference, extracted text, status, error code, warnings, model and parsed time, plus `reviewed_data`/`reviewed_at`. `title`/`description_text` are nullable until parsed. `analysis` keeps the untouched LLM output. |
| 5. APIs | Upload, get, get parsed, re-parse, list (paginated), delete, plus **`PUT /{id}/parsed`** (save review), `DELETE /{id}/parsed/review` (restore extraction) and `GET /{id}/file` (download). |
| 6. Frontend | `/job-descriptions`: upload, list, status polling, role details, required vs preferred skills, technology summary, experience, education, responsibilities with evidence quotes, warnings, **review and correct editor**, restore, retry, download and delete. |

---

## Test results (2026-11-12)

| Suite | Result |
|---|---|
| Backend lint / format / types | ✅ ruff clean (86 files); mypy strict: 0 issues in 85 files |
| Backend unit | ✅ **484 passed** |
| Backend live (PostgreSQL + Memurai + Tesseract) | ✅ **70 passed**, 2 skipped (real Groq; no key) |
| Backend combined coverage | ✅ **95%** |
| Frontend lint / typecheck / build | ✅ 0 problems; `/`, `/resumes`, `/job-descriptions` prerendered |
| Frontend tests | ✅ **124 passed** (10 files) |

Details: [TEST_REPORT.md](TEST_REPORT.md).

---

## Local environment

| Component | Detail |
|---|---|
| Python / Node | 3.12.16 (uv) / 24.x |
| PostgreSQL | 18, `127.0.0.1:5432`, DB `candidate_intel`, owner `candidate_app` (not superuser) |
| Redis | Memurai 8.2, `127.0.0.1:6379` |
| OCR | Tesseract 5.4.0, `eng` |
| LLM | Groq, default model `llama-3.3-70b-versatile`, **key not set** |
| File storage | `backend/storage/resumes/`, `backend/storage/job_descriptions/` (git-ignored) |

---

## Open items

| # | Item | Impact | Action |
|---|---|---|---|
| 1 | **`GROQ_API_KEY` not set** | Structuring cannot run; Phases 1A and 1B cannot be closed | The 4 steps above |
| 2 | In-process background tasks | A restart mid-parse leaves `processing`; re-parse is allowed after 10 minutes | Durable queue (later phase) |
| 3 | No authentication or authorisation | Anyone who can reach the API can read and modify data; it is bound to 127.0.0.1 locally | Required before any shared deployment |
| 4 | Reviewed corrections are not re-grounded | Human edits are trusted as-is (by design); edited items lose their evidence quote | Optional: show "edited by reviewer" in later phases |
| 5 | JD duplicates detected by exact file hash only | The same JD saved in another format counts as a new upload | Text-level de-duplication (later) |
| 6 | Default Groq model may be retired | Parse fails with `llm_error` | Set `LLM_MODEL` |
| 7 | Uncovered rare branches: JD routes 88%, JD processor 87% | Disk-write failure, DB uniqueness race, file-delete error, unexpected-exception path | Fault-injection tests |
| 8 | No candidate-delete endpoint; synthetic E2E records kept | Test data in your database | Delete after the Groq verification |
| 9 | `TestClient` deprecation warning; 7 moderate dev-only `npm audit` findings | Tooling only | Carried over |
| 10 | Nothing committed to git yet | | Make the initial commit |

---

## How to run

```powershell
cd backend
uv run alembic upgrade head          # now at 0003
uv run uvicorn app.main:create_app --factory --reload --port 8000

cd frontend
npm run dev                           # http://localhost:3000/job-descriptions
```

---

[README.md](README.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [API_DOCUMENTATION.md](API_DOCUMENTATION.md) ·
[DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md) · [TEST_REPORT.md](TEST_REPORT.md)
