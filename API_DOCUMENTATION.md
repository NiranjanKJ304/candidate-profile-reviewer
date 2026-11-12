# API Documentation

Base URL (local): `http://localhost:8000`. All versioned endpoints live under
`API_V1_PREFIX` (default `/api/v1`).

Interactive OpenAPI docs are served at `/docs` and `/redoc`, with the schema at
`/openapi.json`, **except when `ENVIRONMENT=production`**, where all three are disabled.

## Conventions

### Request IDs

- Every response includes `X-Request-ID`.
- A client may send its own `X-Request-ID`. It is reused only if it matches
  `^[A-Za-z0-9._-]{1,128}$`; otherwise a new 32-character hex ID is generated. This
  prevents log and header injection.
- The same ID appears in access logs, in application logs and in error bodies.
- CORS exposes this header to browser clients.

### Security headers (every response)

`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
`Cross-Origin-Opener-Policy: same-origin`, `Permissions-Policy: camera=(), microphone=(), geolocation=()`.

### Error envelope

Every error response uses the same shape:

```json
{
  "error": {
    "code": "not_found",
    "message": "Not Found",
    "details": [ ... ],
    "request_id": "3f2c9f0e6b0a4c7f9a3d1e2b4c5d6e7f"
  }
}
```

| HTTP | `code` | Notes |
|---|---|---|
| 400 | `bad_request` | |
| 401 | `unauthorized` | |
| 403 | `forbidden` | |
| 404 | `not_found` | |
| 405 | `method_not_allowed` | |
| 409 | `conflict` | |
| 413 | `payload_too_large` | |
| 415 | `unsupported_media_type` | |
| 422 | `validation_error` | `details[]` holds `type`, `loc` and `msg` only. Submitted values are **never** echoed back, because they may contain PII |
| 429 | `rate_limited` | |
| 500 | `internal_error` | Always the generic message `"An unexpected error occurred"`. Internal details go to the redacted log only |
| 503 | `service_unavailable` / `database_unavailable` | `database_unavailable` when PostgreSQL cannot be reached; driver details stay in the log |
| other | `http_error` | |

`details` and `request_id` are left out when empty.

---

## `GET /api/v1/health`: liveness

Reports that the API process is running. It **does not** contact PostgreSQL or Redis,
so it is safe for container liveness probes.

**200 OK**

```json
{
  "status": "ok",
  "service": "Candidate Intelligence API",
  "version": "0.1.0",
  "environment": "development",
  "timestamp": "2026-11-11T08:51:58.016084Z"
}
```

| Field | Type | Description |
|---|---|---|
| `status` | string | Always `"ok"` when the process responds |
| `service` | string | `APP_NAME` setting |
| `version` | string | Backend package version |
| `environment` | string | `development` \| `test` \| `staging` \| `production` |
| `timestamp` | string (ISO 8601, UTC) | Server time |

Other methods return `405 method_not_allowed`.

---

## `GET /api/v1/ready`: readiness

Probes PostgreSQL (`SELECT 1`) and Redis (`PING`) **concurrently**. Each probe is bounded
by `READINESS_TIMEOUT_SECONDS` (default 2 s), so the endpoint answers within about one
timeout even when both services hang. The response carries `Cache-Control: no-store`.

**200 OK**: all dependencies healthy

```json
{
  "status": "ready",
  "checks": {
    "postgres": { "status": "ok", "latency_ms": 1.84, "error": null },
    "redis":    { "status": "ok", "latency_ms": 0.62, "error": null }
  },
  "timestamp": "2026-11-11T09:00:00.000000Z"
}
```

**503 Service Unavailable**: at least one dependency failed. The body still uses the
readiness schema, *not* the error envelope.

```json
{
  "status": "not_ready",
  "checks": {
    "postgres": { "status": "timeout", "latency_ms": null, "error": "timed out after 2.0s" },
    "redis":    { "status": "error",   "latency_ms": null, "error": "ConnectionError" }
  },
  "timestamp": "2026-11-11T08:52:00.122400Z"
}
```

| Field | Type | Description |
|---|---|---|
| `status` | `"ready"` \| `"not_ready"` | `ready` only if every check is `ok` |
| `checks.<name>.status` | `"ok"` \| `"error"` \| `"timeout"` | Probe outcome |
| `checks.<name>.latency_ms` | number \| null | Probe duration. `null` when the probe did not complete, which is different from `0` |
| `checks.<name>.error` | string \| null | Exception **type only** or the timeout description. For PostgreSQL, the innermost driver cause is reported (for example `InvalidPasswordError`, `InvalidCatalogNameError`, `ConnectionRefusedError`) rather than SQLAlchemy's generic `DBAPIError` wrapper. Never includes hosts or credentials |
| `timestamp` | string (ISO 8601, UTC) | Server time |

---

## `GET /api/v1/system/capabilities`: what parsing can do right now

Non-secret report used by the frontend to explain limitations before upload. API keys are never included.

```json
{
  "llm": { "configured": false, "provider": "groq", "model": "llama-3.3-70b-versatile" },
  "ocr": { "enabled": true, "available": true, "language": "eng" },
  "resume": { "max_bytes": 10485760, "max_pages": 30, "accepted_extensions": [".pdf", ".docx"] }
}
```

---

## Candidates

A resume always belongs to a candidate.

### `POST /api/v1/candidates`

Body (`CandidateCreate`): `{"full_name": "Jordan Sample", "email": "optional@example.org"}`.
`full_name` must be 1-200 characters after trimming. `email` is optional and is normalised to lower case. Unknown fields are rejected.

| Status | Meaning |
|---|---|
| 201 | Created; body `CandidateRead` (`id`, `full_name`, `email`, `phone`, `location`, `created_at`, `updated_at`); `Location` header set |
| 409 | `conflict`: a candidate with this e-mail already exists |
| 422 | `validation_error` |
| 503 | `database_unavailable` |

### `GET /api/v1/candidates/{candidate_id}` → 200 `CandidateRead` · 404

### `GET /api/v1/candidates/{candidate_id}/resumes` → 200 `ResumeRead[]` (newest first) · 404

---

## Resumes

### Parsing lifecycle

```
upload ──202──► pending ──► processing ──► completed   (parsed_data saved)
                                    └────► failed      (parse_error_code + parse_error saved)
failed / completed ──POST /parse──► pending ...       (re-parse; reuses extracted text)
```

Parsing runs in the background after the 202 response. Poll `GET /api/v1/resumes/{id}` until `parse_status` is `completed` or `failed`.

### `POST /api/v1/candidates/{candidate_id}/resumes`: upload

`multipart/form-data` with one field, `file` (`.pdf` or `.docx`).

The file type is decided from the bytes (magic number and internal structure), never from the extension or the client's `Content-Type`. The extension must match the content.

| Status | `error.code` | When |
|---|---|---|
| **202** | | Accepted; body `ResumeRead` with `parse_status: "pending"` |
| 400 | `empty_file` | 0-byte upload |
| 404 | `not_found` | Unknown candidate |
| 409 | `duplicate_resume` | The same file (SHA-256) was already uploaded for this candidate; `details[0].resume_id` names it |
| 413 | `file_too_large` | Larger than `RESUME_MAX_BYTES` (default 10 MB); checked from `Content-Length` before reading |
| 415 | `unsupported_type` | Extension is not `.pdf` / `.docx` |
| 415 | `content_mismatch` | Extension and content disagree (e.g. a DOCX named `.pdf`), or a ZIP that is not a Word document |
| 422 | `corrupted_file` | Unreadable, damaged, or **truncated** PDF/DOCX (MuPDF's silent repair is not trusted) |
| 422 | `encrypted_file` | Password-protected PDF or DOCX |
| 422 | `too_many_pages` | More than `RESUME_MAX_PAGES` (default 30) |
| 422 | `unsafe_file` | Macro-enabled document, or a ZIP that expands to an unsafe size (zip bomb) |
| 422 | `validation_error` | Missing `file` field, or invalid candidate id |
| 503 | `database_unavailable` | The stored file is removed again, so no orphan is left |

### `GET /api/v1/resumes/{resume_id}`: status and structured data

Returns `ResumeDetail` with `Cache-Control: no-store`:

| Field | Type | Notes |
|---|---|---|
| `id`, `candidate_id` | UUID | |
| `original_filename` | string | Sanitised, for display only; the stored file is named by UUID |
| `content_type` | string | Detected type, not the client's claim |
| `file_size_bytes`, `sha256` | int, string | |
| `parse_status` | `pending` / `processing` / `completed` / `failed` | |
| `parse_error_code` | string or null | See the table below |
| `parse_error` | string or null | Human-readable reason |
| `extraction_method` | `pdf_text` / `pdf_ocr` / `docx` / null | `pdf_ocr` = at least one page needed OCR |
| `page_count` | int or null | PDFs only |
| `text_char_count` | int or null | Length of the extracted text; null until extracted |
| `parsed_data` | `ParsedResume` or null | Present only when `completed` |
| `parse_warnings` | string[] | E.g. OCR used, text truncated, values removed by grounding |
| `parser_model` | string or null | Model that produced `parsed_data` (provenance) |
| `parsed_at`, `created_at`, `updated_at` | ISO 8601 | |

Processing error codes (stored on the resume):

| Code | Meaning |
|---|---|
| `no_text` | No readable text, even after OCR |
| `ocr_unavailable` | Scanned PDF, but OCR is disabled or Tesseract data is missing |
| `ocr_failed` | Tesseract failed on a page |
| `llm_not_configured` | No API key for the selected provider |
| `llm_auth_failed` | Provider rejected the key (401/403) |
| `llm_timeout` | No response within `LLM_TIMEOUT_SECONDS` |
| `llm_rate_limited` | 429 after one retry |
| `llm_error` | Other provider or HTTP error |
| `llm_invalid_response` | Output was not valid JSON or failed the `ParsedResume` schema after `LLM_MAX_ATTEMPTS` |
| `storage_error` | The stored file is missing or unreadable |
| `internal_error` | Unexpected failure (details only in the log) |

#### `ParsedResume`

Every field is optional, and every list defaults to `[]`. Missing information is `null`, never guessed.

| Section | Fields |
|---|---|
| `personal_details` | `full_name`, `email`, `phone`, `location`, `linkedin_url`, `github_url`, `portfolio_url`, `other_links[]`, `summary` |
| `education[]` | `institution`, `degree`, `field_of_study`, `start_date`, `end_date`, `grade`, `location` |
| `work_experience[]` | `company`, `title`, `location`, `start_date`, `end_date`, `is_current`, `responsibilities[]`, `technologies[]` |
| `internships[]` | same as `work_experience` |
| `technical_skills[]` | `name` (required), `category` (`language`, `framework`, `library`, `database`, `cloud`, `devops`, `tool`, `other`, or null) |
| `projects[]` | `name`, `description`, `role`, `technologies[]`, `url`, `start_date`, `end_date` |
| `certifications[]` | `name`, `issuer`, `issue_date`, `expiry_date`, `credential_id`, `url` |
| `achievements[]` | `description` (required), `date` |

Dates are kept exactly as written ("Jan 2021", "Present"). After validation, a grounding pass removes any email, phone, link or skill that does not occur in the extracted text, and adds a warning for each removal.

### `GET /api/v1/resumes/{resume_id}/file`: download the original

Returns the original bytes with the detected `Content-Type`, `Content-Disposition: attachment`, `Content-Security-Policy: default-src 'none'; sandbox` and `Cache-Control: private, no-store`. Returns 404 if the resume or its stored file does not exist.

### `POST /api/v1/resumes/{resume_id}/parse`: re-parse

Use this after adding the LLM key, or after a failure. It reuses the stored extracted text when present.

| Status | Meaning |
|---|---|
| 202 | Queued; body `ResumeRead` with `parse_status: "pending"` |
| 404 | Unknown resume |
| 409 | Already `pending`/`processing` and updated within the last 10 minutes (older rows are treated as abandoned and may be re-parsed) |

### `DELETE /api/v1/resumes/{resume_id}` → 204 · 404

Deletes the database row, including `parsed_data` and the extracted text, and then the stored file. An evaluation that referenced the resume keeps its history with `resume_id = NULL`.

---

## Job descriptions

Upload a JD file; text extraction (with OCR for scanned PDFs) and LLM structuring run in the
background after a 202 response. Poll `GET /api/v1/job-descriptions/{id}` until `parse_status`
is `completed` or `failed`. The lifecycle and processing error codes are the same as for resumes
(see [Resumes](#resumes)).

### `POST /api/v1/job-descriptions/upload`

`multipart/form-data`, field `file`: `.pdf`, `.docx` or `.txt` (max `JOB_DESCRIPTION_MAX_BYTES`,
default 5 MB; PDFs max `JOB_DESCRIPTION_MAX_PAGES`, default 20). Type is decided from the bytes.
TXT accepts UTF-8 (with or without BOM), UTF-16 (with BOM) and cp1252; content containing binary
data is rejected.

| Status | `error.code` | When |
|---|---|---|
| **202** | | Accepted; body `JobDescriptionSummary` with `parse_status: "pending"`, `title: null` |
| 400 | `empty_file` | 0 bytes, or a text file with only whitespace |
| 409 | `duplicate_job_description` | Same file (SHA-256) already uploaded; `details[0].job_description_id` names it |
| 413 | `file_too_large` | Over the size limit |
| 415 | `unsupported_type` / `content_mismatch` | Not `.pdf`/`.docx`/`.txt`, extension and content disagree, or binary data in a `.txt` |
| 422 | `corrupted_file` / `encrypted_file` / `too_many_pages` / `unsafe_file` | As for resumes |
| 503 | `database_unavailable` | The stored file is removed again |

### `GET /api/v1/job-descriptions?limit=50&offset=0`

Newest first. `limit` 1-200 (default 50), `offset` ≥ 0.
Response: `{"items": JobDescriptionSummary[], "total": int, "limit": int, "offset": int}`.

`JobDescriptionSummary`: `id`, `title` (null until parsed), `company`, `original_filename`,
`content_type`, `file_size_bytes`, `parse_status`, `parse_error_code`, `extraction_method`
(`pdf_text` / `pdf_ocr` / `docx` / `txt`), `has_review`, `created_at`, `updated_at`.

### `GET /api/v1/job-descriptions/{id}`

`JobDescriptionDetail` = summary + `sha256`, `page_count`, `parse_error`, `parse_warnings[]`,
`parser_model`, `parsed_at`, `reviewed_at`, `text_char_count`. `Cache-Control: no-store`. 404 if unknown.

### `GET /api/v1/job-descriptions/{id}/parsed`

Returns the structured JD. 409 `parse_not_completed` until parsing succeeds.

| Field | Meaning |
|---|---|
| `source` | `"reviewed"` when a human-corrected version exists, else `"extracted"` |
| `data` | The **effective** `ParsedJobDescription` (reviewed if present) |
| `extracted` | The untouched, grounded LLM extraction (always kept, for comparison) |
| `technology_summary` | `programming_languages`, `frameworks_and_libraries`, `databases`, `cloud_and_devops`, `tools`, `other`, derived from the skills in `data` |
| `parser_model`, `parsed_at`, `reviewed_at`, `warnings[]` | Provenance |

#### `ParsedJobDescription`

Everything is optional. Missing requirements are `null` / `[]`, never guessed. `evidence` is a
verbatim quote from the JD; it is null for items a reviewer added or edited.

| Field | Shape |
|---|---|
| `job_title`, `company_name`, `location`, `job_summary` | string or null |
| `required_skills[]`, `preferred_skills[]` | `{name, category, evidence}`; `category` is one of `language`, `framework`, `library`, `database`, `cloud`, `devops`, `tool`, `other`, or null. A skill never appears in both lists |
| `minimum_experience`, `preferred_experience` | `{years_min, years_max, description, evidence}` or null; years 0-50, max ≥ min |
| `education_requirements[]` | `{level, field_of_study, mandatory, description, evidence}`; `level` is one of `high_school`, `diploma`, `associate`, `bachelor`, `master`, `doctorate`, `other` |
| `responsibilities[]`, `required_qualifications[]`, `soft_skills[]` | `{text, evidence}` |
| `seniority` | `{level, evidence}`; `level` is one of `intern`, `junior`, `mid`, `senior`, `lead`, `principal`, `manager`, or null |

Grounding (LLM output only):
- removes skills whose name or quote is not in the JD text;
- removes responsibilities, qualifications, soft skills, education and experience items whose quote is not found;
- removes experience whose years do not appear in its quote;
- clears seniority that is not stated;
- keeps a skill listed as both required and preferred as required only.

Each removal is reported in `warnings`.

### `PUT /api/v1/job-descriptions/{id}/parsed`: save a reviewed version

Body: a full `ParsedJobDescription` (validated; unknown keys rejected). It is stored as
`reviewed_data`; `extracted` is not modified. The record's `title` and `company` follow the
reviewed values. Returns `ParsedJobDescriptionResponse` with `source: "reviewed"`.
409 `parse_not_completed` if the JD was never parsed; 422 for invalid bodies.

### `DELETE /api/v1/job-descriptions/{id}/parsed/review`: restore the extraction

Drops the reviewed version. Returns the response with `source: "extracted"`.

### `POST /api/v1/job-descriptions/{id}/parse`: re-parse

202 queued. It reuses the extracted text when present. A re-parse **never overwrites** a reviewed version; a warning is added instead. 409 if already `pending`/`processing` (younger than 10 minutes) or there is nothing to parse.

### `GET /api/v1/job-descriptions/{id}/file`

The original bytes as an attachment, with a sandbox CSP and `no-store`. 404 if missing.

### `DELETE /api/v1/job-descriptions/{id}` → 204

Deletes the row (including extracted text, extraction and review) and the stored file.
409 `job_description_in_use` if evaluations reference it (history is kept). 404 if unknown.

### `GET /api/v1/system/capabilities`

Now also reports `job_description: {max_bytes, max_pages, accepted_extensions: [".pdf", ".docx", ".txt"]}`.

---

## Domain schemas not yet exposed

`app/schemas/domain.py` also defines contracts for later phases. **No endpoint uses these yet:**

| Schema | Direction | Validation highlights |
|---|---|---|
| `JobDescriptionCreate` | input | `title` 1-300 characters, `description_text` 1-100,000 characters, `company` optional, `source_url` absolute http(s) only |
| `DeveloperProfileCreate` | input | `platform` one of github, leetcode, hackerrank, codeforces, codechef, kaggle; `username` matches `[A-Za-z0-9._-]`, 1-100 characters, starting with a letter or digit; `profile_url` http(s) only |
| `DeveloperProfileRead` | output | `ownership_status`, `collection_status`, provenance (`source_url`, `collected_at`) |
| `EvaluationRead` | output | `status`, `overall_score` null or 0-100 (null means not scored, never 0), `result`, `model_name` |

## Endpoints planned for later phases

Developer-profile collection, evaluation and report endpoints
will be documented here as each phase delivers them.
