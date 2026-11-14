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
  "llm": { "configured": false, "provider": "groq", "model": "openai/gpt-oss-120b" },
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

Also reports `job_description: {max_bytes, max_pages, accepted_extensions}`, `github: {enabled, authenticated, accepted_inputs}`, `codeforces: {enabled, accepted_inputs, submission_limit}` and `leetcode: {enabled, integration_mode: "manual_self_reported", sync_supported: false, accepted_inputs}`.

---

## GitHub developer profiles (Phase 2)

Enabled only when `ENABLED_PLATFORMS` includes `github`. Otherwise `POST` endpoints return
503 `integration_disabled`; stored data stays readable. All GitHub calls go to the fixed host
`https://api.github.com` (REST API version 2022-11-28) and use only the validated username.
Submitted URLs are never fetched.

### `POST /api/v1/candidates/{candidate_id}/github`

Body: `{"profile": "<username | @username | https://github.com/{username}>"}` (1-300 characters,
unknown keys rejected). It creates the association (`ownership_status: "unverified"`) and starts a
background sync. Response **202** `GitHubProfileResponse` with `sync_status: "pending"`.

| Status | `error.code` | When |
|---|---|---|
| 404 | `not_found` | Unknown candidate |
| 409 | `duplicate_profile` | The candidate already has a GitHub profile (same name, case-insensitive, or a different one); `details[0]` gives `profile_id` and `username` |
| 422 | `empty`, `too_long`, `malformed`, `malformed_url`, `unsupported_scheme`, `unsupported_domain`, `not_a_profile`, `invalid_username` | Input rejected; no network call is made |
| 503 | `integration_disabled` / `database_unavailable` | |

Accepted: GitHub usernames (1-39 characters, letters, digits and single hyphens, no leading or
trailing hyphen) and URLs on `github.com` / `www.github.com` with exactly one path segment.
Rejected: other hosts and subdomains (`gist.`, `api.`), look-alike domains, credentials in
URLs, ports, `ftp:`, `file:` and `javascript:` schemes, repository and sub-page URLs, and
reserved paths (`settings`, `orgs`, …).

### `GET /api/v1/candidates/{candidate_id}/github`

`GitHubProfileResponse`:

| Field | Meaning |
|---|---|
| `id`, `candidate_id`, `platform` (`github`), `username`, `profile_url` | Association (`username` becomes GitHub's canonical login after the first sync) |
| `ownership_status` | `unverified`. A submitted URL is never treated as proof of ownership |
| `collection_status` | `not_collected` / `collected` / `unavailable` (account missing or an organisation) / `failed` (never collected successfully) |
| `sync_status` | `pending` / `processing` / `completed` / `failed` (last run) |
| `sync_error_code`, `sync_error` | `invalid_profile`, `not_a_user`, `rate_limited`, `permission_denied`, `auth_failed`, `network_error`, `timeout`, `api_error`, `integration_disabled`, `internal_error` |
| `sync_warnings[]` | Partial data, truncation, rename, the same account linked to another candidate, no token |
| `last_sync_attempt_at`, `collected_at` | Last attempt and last **successful** collection |
| `data` | Last successful `GitHubSnapshot`, kept even when a later retry fails |

`GitHubSnapshot` sections each carry `status`, `note` and `provenance`
`{source_platform: "github", source_url, api_reference, collected_at}`:

| Section | Content |
|---|---|
| `profile` | `login`, `id`, `account_type`, `name`, `bio`, `company`, `location`, `blog`, `public_repos`, `followers`, `following`, `created_at`, `updated_at`, `html_url` (no e-mail) |
| `repositories` | Up to `GITHUB_MAX_REPO_PAGES` x 100, most recently pushed first: `name`, `full_name`, `description`, `primary_language`, `html_url`, `created_at`/`updated_at`/`pushed_at`, `stargazers_count`, `forks_count`, `is_fork`, `is_archived`, `topics`, `readme` (`status`, `excerpt` up to 3,000 characters, `size_bytes`, `truncated`; fetched for up to `GITHUB_README_LIMIT` non-fork, non-archived repositories; null when not requested). Also `truncated`, `skipped_malformed`, `reported_public_repos` |
| `languages`, `topics` | Counts across non-fork repositories (repositories per language or topic, not code volume) |
| `activity` | From the public events feed (at most 90 days / 300 events): `events_analyzed`, `event_counts`, `push_events`, `commits_reported` (null if not reported), `active_repositories`, first/last event times. **No commit messages or author e-mails** |
| `pull_requests`, `issues` | Search API: `total_count` (GitHub's estimate), `incomplete_results`, the most recent `GITHUB_SEARCH_ITEMS` items (`title`, `repository`, `state`, dates, `merged`, `html_url`) |
| `contribution_calendar` | Always `unavailable`: not offered by the REST API |

Availability values: `available`, `empty` (nothing there, which is **not** evidence of missing
skills), `unavailable`, `permission_denied`, `rate_limited`, `network_error`, `error`,
`not_collected`. Snapshot-level: `collected_at`, `authenticated`, `warnings`, `api_requests`.

404 if the candidate has no GitHub profile.

### `POST /api/v1/candidates/{candidate_id}/github/sync`

Trigger or retry. 202 with `sync_status: "pending"`. 409 if a sync started less than 10 minutes
ago is still pending or processing. 503 when disabled.

### `GET /api/v1/candidates/{candidate_id}/github/repositories`

From the last snapshot (no API call). Query: `limit` 1-300 (default 50), `offset`,
`include_forks` (true), `include_archived` (true), `language` (case-insensitive).
Response: `{items, total, limit, offset, status, collected_at}`. Before any successful
collection: `status: "not_collected"`, `items: []`.

### `DELETE /api/v1/candidates/{candidate_id}/github` → 204

Removes the association and its collected data. Deleting a candidate cascades.

### `GET /api/v1/integrations/github/status`

`{enabled, authenticated, reachable, rate_limits: [{resource, limit, remaining, reset_at}], error, limits}`.
When enabled, it calls GitHub's `GET /rate_limit`, which does not count against the quota.
`reachable` is null when the integration is disabled. Token values are never returned.

### `GET /api/v1/candidates?limit=50&offset=0`

New in Phase 2: lists candidates, newest first:
`{items: CandidateRead[], total, limit, offset}`. `limit` 1-200.

### GitHub API budget and limits

| | Unauthenticated | With `GITHUB_TOKEN` (no scopes needed) |
|---|---|---|
| Core REST | 60 / hour | 5,000 / hour |
| Search | 10 / minute | 30 / minute |

Per sync, with the defaults: 1 profile + 1-3 repository pages + up to 10 READMEs + 1-3 event
pages (core) and 2 searches. Primary limit exhausted (`x-ratelimit-remaining: 0`) → no
waiting; sections are marked `rate_limited` and the reset time is in the note. Secondary
limits with `retry-after` ≤ 10 s are retried; 5xx and network errors are retried with backoff
(`GITHUB_MAX_RETRIES`). Redirects and pagination links are followed only within
`https://api.github.com`.

---

## Codeforces developer profiles (Phase 3)

Enabled only when `ENABLED_PLATFORMS` includes `codeforces`. Otherwise add, sync and retry
return 503 `integration_disabled`; stored data stays readable. Every call goes to the fixed
base `https://codeforces.com/api/`, uses only the documented public methods `user.info`,
`user.rating` and `user.status` (an allow-list in the client), and passes only the validated
handle. No API key is used, and submitted URLs are never fetched.

### `POST /api/v1/candidates/{candidate_id}/codeforces`

Body: `{"profile": "<handle | https://codeforces.com/profile/{handle}>"}` (1-300 characters,
unknown keys rejected). It creates the association (`ownership_status: "unverified"`) and starts
a background sync. Response **202** `CodeforcesProfileResponse` with `sync_status: "pending"`.

| Status | `error.code` | When |
|---|---|---|
| 404 | `not_found` | Unknown candidate |
| 409 | `duplicate_profile` | The candidate already has a Codeforces profile (same handle, case-insensitive, or a different one); `details[0]` gives `profile_id` and `handle` |
| 422 | `empty`, `too_long`, `malformed`, `malformed_url`, `unsupported_scheme`, `unsupported_domain`, `not_a_profile`, `invalid_handle` | Input rejected; no network call is made |
| 503 | `integration_disabled` / `database_unavailable` | |

Accepted handles: at most 24 characters from latin letters, digits, `_`, `-` and `.`, with at
least one letter or digit. URLs must be on `codeforces.com` / `www.codeforces.com` with the
path `/profile/{handle}`. Rejected: other hosts (`codeforces.ru`, `m1.codeforces.com`,
look-alike domains), credentials, ports, non-http schemes, percent-encoded or backslash
paths, contest, problem, blog and submission URLs, and `;` (which would query several
users in `user.info`).

### `GET /api/v1/candidates/{candidate_id}/codeforces`

`CodeforcesProfileResponse`:

| Field | Meaning |
|---|---|
| `id`, `candidate_id`, `platform` (`codeforces`), `handle`, `profile_url` | Association (`handle` becomes the current handle reported by Codeforces after a sync) |
| `ownership_status` | `unverified`. A submitted handle is never treated as proof of ownership |
| `collection_status` | `not_collected` / `collected` / `unavailable` (no such user) / `failed` (never collected successfully) |
| `sync_status` | `pending` / `processing` / `completed` / `failed` (last run) |
| `sync_error_code`, `sync_error` | `invalid_profile`, `rate_limited`, `network_error`, `timeout`, `api_error`, `integration_disabled`, `internal_error` |
| `sync_warnings[]` | Truncated window, malformed records skipped, stopped early, unrated account, handle changed, account also linked to another candidate |
| `last_sync_attempt_at`, `collected_at` | Last attempt and last **successful** collection |
| `freshness` | `{collected_at, age_seconds, stale, stale_after_hours: 168}`: `stale` after 7 days or if never collected |
| `data` | Last successful `CodeforcesSnapshot`, kept when a later sync fails |

`CodeforcesSnapshot` sections each carry `status`, `note` and `provenance`
`{source_platform: "codeforces", source_url, api_method, collected_at}`:

| Section | API method | Content |
|---|---|---|
| `profile` | `user.info` | `handle`, `first_name`, `last_name`, `country`, `city`, `organization`, `rating`, `max_rating`, `rank`, `max_rank` (null if unrated), `contribution`, `registered_at`, `last_online_at`, `profile_url`. Not stored: e-mail, VK/OpenID ids, avatar |
| `rating_history` | `user.rating` | Rated contests, oldest first: `contest_id`, `contest_name`, `rank`, `old_rating`, `new_rating`, `delta`, `rating_updated_at`, `contest_url`. `skipped_malformed` |
| `submissions` | `user.status` | The most recent `CODEFORCES_SUBMISSION_LIMIT` (default 500, hard maximum 1000), newest first: `id`, `contest_id`, `problem_index`, `problem_name`, `problem_rating` (null if the problem has none), `tags`, `programming_language`, `verdict` (null while judging), `passed_test_count`, `submitted_at`, `relative_time_seconds` (null for practice), `participant_type`, `problem_key`, `problem_url`. Also `requested_limit`, `truncated` (more exist), `skipped_malformed`, `window_start`, `window_end`. No source code (never requested), no team-mate handles |
| `analytics` | derived | `lifetime` (scope `lifetime`): `current_rating`, `max_rating`, `rank`, `max_rank`, `rated_contests` (null if `user.rating` failed). `submissions` (scope `collected_window`, **null when submissions could not be collected, never zeros**): see definitions below |

**Collected-window statistics** (`analytics.submissions`; reproducible from `submissions.items`):

| Field | Definition |
|---|---|
| `total_submissions` | Submissions collected |
| `accepted_submissions` | Verdict `OK` |
| `pending_submissions` | No verdict yet, `TESTING` or `SUBMITTED` |
| `rejected_submissions` | Any other verdict (WRONG_ANSWER, TIME_LIMIT_EXCEEDED, COMPILATION_ERROR, SKIPPED, CHALLENGED, ...) |
| `attempted_unique_problems` / `accepted_unique_problems` | Distinct `problem_key` (contest id + index) attempted / with at least one `OK` |
| `accepted_by_rating[]` | `{rating, problems}`: accepted unique problems per problem rating (`rating: null` = unrated problem) |
| `accepted_by_tag[]` | `{name, count}`: accepted unique problems per tag |
| `verdicts[]`, `languages[]` | `{name, count}` over submissions |
| `monthly_activity[]` | `{month: "YYYY-MM", submissions, accepted}` (UTC) |
| `contests_in_window` | Distinct contests with `CONTESTANT` or `OUT_OF_COMPETITION` submissions |
| `window_start`, `window_end`, `truncated` | Time span of the window; `truncated` means older submissions exist |

These numbers describe the collected sample only. They are not lifetime totals, and neither
they nor ratings are a complete measure of programming ability.

### `POST /api/v1/candidates/{candidate_id}/codeforces/sync`

Refresh at any time. 202 with `sync_status: "pending"`. 409 if a sync started less than 10
minutes ago is still pending or processing. 503 when disabled.

### `POST /api/v1/candidates/{candidate_id}/codeforces/retry`

Retry a failed sync. 202 when the last sync failed; otherwise 409 `not_failed` (use `/sync`).
503 when disabled.

### `GET /api/v1/candidates/{candidate_id}/codeforces/submissions`

From the last snapshot (no API call). Query: `limit` 1-1000 (default 50), `offset`, `verdict`,
`language`, `tag` (all case-insensitive exact matches). Response:
`{items, total, limit, offset, status, truncated, collected_at}`. Before any successful
collection: `status: "not_collected"`, `items: []`.

### `GET /api/v1/candidates/{candidate_id}/codeforces/rating-history`

From the last snapshot. Query: `limit` 1-1000 (default 100), `offset`. Response:
`{items, total, limit, offset, status, collected_at}`, oldest contest first.

### `DELETE /api/v1/candidates/{candidate_id}/codeforces` → 204

Removes the association and its collected data. Other platforms are unaffected. Deleting a
candidate cascades.

### `GET /api/v1/integrations/codeforces/status`

`{enabled, api_base_url, api_methods, min_request_interval_seconds, submission_limit,
submission_hard_max, requests_made, last_success_at, last_error, last_error_at, note}`.
**It makes no request to Codeforces.** It reports configuration and the state observed by
this server process.

### Codeforces API usage and limits

- Codeforces asks for at most about one call every 2 seconds. Every request passes one
  shared pacer (asyncio lock plus `CODEFORCES_MIN_REQUEST_INTERVAL_SECONDS`, minimum 2),
  so concurrent syncs are serialised.
- One sync makes 3 requests with the defaults: `user.info`, `user.rating` and one
  `user.status` page (`count` = limit + 1, to detect truncation).
- `status: "FAILED"`: a "handle not found" comment means `invalid_profile`. "Call limit
  exceeded" and HTTP 429 are retried with backoff (`CODEFORCES_MAX_RETRIES`), then
  `rate_limited`. Other comments mean `api_error`. HTTP 5xx without a FAILED body, timeouts
  and network errors are retried, then `network_error` / `timeout`.
- `user.info` failing aborts the sync, and the previous snapshot is kept. A failing
  `user.rating` or `user.status` gives a partial snapshot with that section marked. After a
  rate limit, later methods are skipped (`rate_limited`) without further calls.
- Redirects are followed once, and only to `https://codeforces.com/api/`.
- No response cache: reads are served from the stored snapshot, and syncs are user-triggered.

---

## LeetCode profiles (Phase 4): manual, self-reported evidence only

**Integration type: manual candidate-submitted evidence.** LeetCode offers no official or
permitted data API, and its Terms of Service forbid scraping (findings and sources:
`GET /api/v1/integrations/leetcode/status`, ARCHITECTURE.md). These endpoints **never
contact LeetCode**: there is no sync and no retry, and no username is checked against
LeetCode. Everything stored is labelled `self_reported` / `not_verified`.

Enabled only when `ENABLED_PLATFORMS` includes `leetcode`. Otherwise link and evidence
submission return 503 `integration_disabled`; stored data stays readable and removable.

### `POST /api/v1/candidates/{candidate_id}/leetcode` → 201

Body: `{"profile": "<username | https://leetcode.com/u/{username}/ | https://leetcode.com/{username}/>"}`
(1-300 characters, unknown keys rejected). It creates the association with
`ownership_status: "unverified"`, `collection_status: "unavailable"`,
`sync_status: "not_applicable"`, `evidence: null`.

| Status | `error.code` | When |
|---|---|---|
| 404 | `not_found` | Unknown candidate |
| 409 | `duplicate_profile` | The candidate already has a LeetCode profile (case-insensitive) |
| 422 | `empty`, `too_long`, `malformed`, `malformed_url`, `unsupported_scheme`, `unsupported_domain`, `not_a_profile`, `invalid_username` | Input rejected |
| 503 | `integration_disabled` / `database_unavailable` | |

Usernames: up to 40 latin letters, digits, `_`, `-` or `.`, with at least one letter or digit,
and not a site page name. URLs: `leetcode.com` / `www.leetcode.com` only (`leetcode.cn` is a
separate service and is rejected); `/u/{username}` or the legacy single segment. Problem,
contest, discuss, `/graphql`, `/api`, `/submissions` and other pages are rejected.

### `GET /api/v1/candidates/{candidate_id}/leetcode`

`LeetCodeProfileResponse`:

| Field | Meaning |
|---|---|
| `id`, `candidate_id`, `platform` (`leetcode`), `username`, `profile_url` | Association; `profile_url` is canonical `https://leetcode.com/u/{username}/` and is never fetched |
| `ownership_status` | `unverified` |
| `collection_status` | Always `unavailable`: nothing is collected from LeetCode |
| `sync_status` | Always `not_applicable` |
| `integration_mode` | `manual_self_reported` |
| `platform_data` | `{status: "unavailable", reason, sources}` |
| `evidence` | `LeetCodeEvidence` or `null` (none submitted) |
| `freshness` | `{submitted_at, as_of, age_days, stale, stale_after_days}`: age from `as_of` (or the submission date); `stale` after `LEETCODE_EVIDENCE_STALE_AFTER_DAYS` (default 30) or when there is no evidence |
| `warnings` | Username not checked against LeetCode; ownership not verified; self-reported and not verified; no "as of" date |

`LeetCodeEvidence` (stored in `developer_profiles.self_reported_data`, **never** in
`collected_data`; strict schema that accepts only `source_type: "self_reported"` and
`verification_status: "not_verified"`):

| Field | Content |
|---|---|
| `reported_by` | `candidate` or `recruiter` (entered from the candidate's statement) |
| `submitted_at`, `as_of`, `revision` | When entered; the date the figures describe; increments on each replacement |
| `solved` | `total`, `easy`, `medium`, `hard` |
| `contest` | `rating`, `attended`, `top_percentage` |
| `contest_history.items[]` | `contest_name`, `rank`, `rating_after`, `contest_date` |
| `languages.items[]`, `topics.items[]` | `name`, `problems_solved` (optional) |
| `notes` | Free text (≤ 1,000 characters, untrusted, rendered as text) |

Every section has `status` (`available` = provided, `not_collected` = **not provided:
unknown, not zero**), `note` and `provenance` `{source_platform: "leetcode", source_type:
"self_reported", verification_status: "not_verified", method: "manual_entry", reported_by,
claimed_profile_url, submitted_at}`. A value that was not provided is `null`; a real 0 stays 0.

### `PUT /api/v1/candidates/{candidate_id}/leetcode/evidence` → 200

Submit or replace (not merge) the evidence. Body `LeetCodeEvidenceInput` (unknown keys
rejected):

| Field | Rule |
|---|---|
| `reported_by` | **Required**: `candidate` or `recruiter` |
| `as_of` | Date; not in the future (1 day of leeway for timezones), not before 2015-01-01 |
| `total_solved`, `easy_solved`, `medium_solved`, `hard_solved` | Integers 0-10,000 (strict: no `true`, no strings) |
| `contest_rating` 0-5,000 · `contests_attended` 0-5,000 · `contest_top_percentage` > 0 and ≤ 100 | |
| `contest_history[]` (≤ 100) | `contest_name` (required, ≤ 120), `rank` ≥ 1, `rating_after` 0-5,000, `contest_date` |
| `languages[]` (≤ 30), `topics[]` (≤ 60) | `name` (≤ 60), `problems_solved` 0-10,000 |
| `notes` | ≤ 1,000 characters |

Cross-field rules: at least one evidence value; easy + medium + hard ≤ total (and = total
when all four are given); each language or topic count ≤ total; no contest rating, top % or
history when `contests_attended` is 0; history entries ≤ contests attended; no duplicate
names (case-insensitive) or contests; names are whitespace-normalised; control characters
are rejected. Errors: 422 `validation_error` with `details[].msg` (submitted values are
never echoed). 404 without a profile; 503 when disabled.

### `DELETE /api/v1/candidates/{candidate_id}/leetcode/evidence` → 204

Clears the evidence and keeps the association.

### `DELETE /api/v1/candidates/{candidate_id}/leetcode` → 204

Removes the association and its evidence. Other platforms are unaffected. Deleting a
candidate cascades.

### `GET /api/v1/integrations/leetcode/status`

`{enabled, integration_mode: "manual_self_reported", platform_api: "unavailable",
sync_supported: false, contacts_leetcode: false, investigated_on, findings[], sources[],
evidence_stale_after_days}`.

There are **no** `/sync` or `/retry` endpoints for LeetCode.

---

## Evaluations (Phase 5): is a candidate suitable for a job description?

The evaluation compares a candidate's evidence with a parsed JD:
1. A **deterministic evidence bundle** and **requirement matching**.
2. An **LLM verdict per requirement** that must cite evidence ids.
3. **Backend guards**.
4. A **backend-computed score**.

Personal details are removed before the LLM call. Work runs in the background.

### `POST /api/v1/evaluations` → 202

Body: `{"candidate_id": uuid, "job_description_id": uuid, "resume_id": uuid | omitted}` (unknown
keys rejected). Without `resume_id`, the candidate's most recent parsed resume is used (or none).
Linked GitHub / Codeforces snapshots and LeetCode self-reported evidence are always included.

| Status | `error.code` | When |
|---|---|---|
| 404 | `not_found` | Unknown candidate or JD |
| 409 | `jd_not_parsed` | JD not parsed (or unreadable) |
| 409 | `resume_not_parsed` | The given resume is not parsed |
| 409 | `evaluation_in_progress` | Same candidate + JD already running (< 10 min); `details[0].evaluation_id` |
| 422 | `invalid_resume` / `validation_error` | Resume missing or belongs to another candidate / bad body |
| 503 | `llm_not_configured` / `database_unavailable` | |

### `GET /api/v1/evaluations/{id}`

`EvaluationResponse`:

| Field | Meaning |
|---|---|
| `id`, `candidate_id`, `job_description_id`, `resume_id` | What was evaluated |
| `status` | `pending` / `running` / `completed` / `failed` |
| `overall_score` | Number 0–100, or **null** (not scored yet, or too little evidence); never 0 by default |
| `fit_label` | `strong` / `moderate` / `weak` / `insufficient_evidence` |
| `job_title`, `model_name`, `created_at`, `completed_at` | |
| `error_code`, `error_message` | `source_missing`, `jd_not_parsed`, `jd_has_no_requirements`, `no_evidence`, `llm_not_configured`, `llm_auth_failed`, `llm_timeout`, `llm_rate_limited`, `llm_error`, `llm_invalid_response`, `internal_error` |
| `warnings[]` | Adjusted verdicts, verdicts to review, missing resume, truncated requirements, retries |
| `result` | `EvaluationResult` (below) when completed |

`EvaluationResult`:

| Field | Content |
|---|---|
| `jd_version` | `reviewed` (human-corrected) or `extracted` |
| `sources[]` | Per source (`resume`, `github`, `codeforces`, `leetcode`): `used` / `not_linked` / `not_collected` / `unavailable`, with a note. Not linked is not evidence of missing skills |
| `evidence[]` | `{id, source, trust, kind, text, skills, source_url, sent_to_llm}`. `sent_to_llm: false` = left out to fit the LLM request limit (cannot back a verdict). Ids: `R*` resume, `G*` GitHub, `C*` Codeforces, `L*` LeetCode. Trust: `platform_collected`, `resume_claim`, `self_reported`. `text` is identity-redacted; `source_url` is **not** sent to the LLM |
| `experience` | `{years, entries_used, entries_unparsed, method}`: non-overlapping work-experience months from resume dates (internships excluded); `years: null` = unknown |
| `requirements[]` | `{id (Q*), kind (required/preferred), type (skill/experience/education/qualification), text, baseline_status, baseline_evidence_ids, verdict, evidence_ids, rationale, weight, credit, adjusted}` |
| `score` | `{overall, fit_label, coverage, required_met, required_total, preferred_met, preferred_total, unmet_required[], unevidenced_required[], method}` |
| `strengths[]`, `discrepancies[]` | `{text, evidence_ids}` |
| `gaps[]` | `{text, requirement_ids}` |
| `summary` | Model-written; may contain mistakes, the requirement verdicts are authoritative |
| `privacy` | `identity_removed`, `source_urls_sent_to_llm: false`, redaction counts (never the values) |
| `decision_support_notice`, `prompt_version` | |

**Requirements scored:** required and preferred skills (alias-normalised, duplicates merged),
minimum (required) and preferred experience, education (required only when the JD marks it
mandatory), and required qualifications that do not repeat another requirement.
Responsibilities are context only; soft skills are not scored.

**Baseline** (`baseline_status`, deterministic): `platform_evidence`, `resume_claim_only`,
`self_reported_only`, `no_evidence`; `not_applicable` for non-skill requirements.

**Verdicts and guards:**
- `met` / `partially_met` must cite existing evidence, otherwise they become `insufficient_evidence`.
- `not_met` must cite the evidence that shows it, otherwise it becomes `insufficient_evidence`.
- Missing verdicts become `insufficient_evidence`.
- Unknown ids are removed.
- A `not_met` verdict on a skill with platform evidence produces a review warning.
- Every change is recorded in `adjusted`.

**Score** (`score.method`):
- Weights: required 2, preferred 1.
- Points: met 1, partial 0.5, not met 0, insufficient 0.
- A met or partial verdict backed **only** by self-reported evidence counts ×0.75.
- `overall = 100 × credit / total weight`.
- Coverage below 50% gives `overall: null`.
- Labels: Strong ≥ 75, Moderate ≥ 50, otherwise Weak. Capped at Moderate when a required item is not met.

**Request size:** the prompt is fitted to `LLM_EVALUATION_MAX_PROMPT_TOKENS` (default 4,500;
Groq's free tier allows 8,000 tokens per minute including the answer). Long evidence texts
are shortened, and the least relevant items are left out first; requirements and each
requirement's best three evidence items are kept. If even that does not fit, the evaluation
fails with `llm_error` and an explanation, and no request is sent. A provider HTTP 413 is reported
with its limit and requested token numbers.

### `GET /api/v1/evaluations?candidate_id=&job_description_id=&limit=50&offset=0`

Newest first; `{items: EvaluationSummary[], total, limit, offset}` (summaries omit `result` and
`warnings`). `limit` 1–200.

### `POST /api/v1/evaluations/{id}/rerun` → 202

Runs again with the current evidence (and the latest parsed resume if the original was
deleted). 409 `evaluation_in_progress` while running; 503 `llm_not_configured`.

### `DELETE /api/v1/evaluations/{id}` → 204

A JD cannot be deleted while evaluations reference it (409 `job_description_in_use`).
Deleting a candidate deletes their evaluations.

---

## Domain schemas not yet exposed

`app/schemas/domain.py` also defines contracts for later phases. **No endpoint uses these yet:**

| Schema | Direction | Validation highlights |
|---|---|---|
| `JobDescriptionCreate` | input | `title` 1-300 characters, `description_text` 1-100,000 characters, `company` optional, `source_url` absolute http(s) only |
| `DeveloperProfileCreate` (generic; GitHub and Codeforces use `GitHubProfileCreate` / `CodeforcesProfileCreate`) | input | `platform` one of github, leetcode, hackerrank, codeforces, codechef, kaggle; `username` matches `[A-Za-z0-9._-]`, 1-100 characters, starting with a letter or digit; `profile_url` http(s) only |
| `DeveloperProfileRead` | output | `ownership_status`, `collection_status`, provenance (`source_url`, `collected_at`) |
| `EvaluationRead` (superseded by `EvaluationResponse`, Phase 5) | output | `status`, `overall_score` null or 0-100 (null means not scored, never 0), `result`, `model_name` |

## Endpoints planned for later phases

Other platforms, evaluation and report endpoints
will be documented here as each phase delivers them.
