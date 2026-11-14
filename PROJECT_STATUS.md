# Project Status

**Project:** AI-Powered Multi-Platform Candidate Intelligence and Recruitment Evaluation System
**Last updated:** 2026-11-14

| Phase | Status |
|---|---|
| Phase 0: Foundation | ✅ Complete and verified |
| Phase 1A: Resume Parsing Engine | ✅ Complete: real Groq structuring verified (`openai/gpt-oss-120b`) |
| Phase 1B: Job Description Parsing Engine | ✅ Complete: real Groq structuring verified |
| Phase 2: GitHub Developer Profile Integration | ✅ Complete and verified (real GitHub API) |
| Phase 3: Codeforces Developer Profile Integration | ✅ Complete and verified (real Codeforces API) |
| Phase 4: LeetCode | ✅ Complete as a **manual, self-reported evidence** workflow (no official or permitted API exists) |
| **Phase 5: Candidate–JD Evaluation** | ✅ **Complete and verified**, including real Groq evaluations and a full real end-to-end run |
| Phase 6+ (interview questions, recruiter reports, other platforms, authentication) | ⚪ Not started |

---

## Where to see whether a candidate suits a job

**http://localhost:3000/evaluations** (navigation: **Evaluate**). Choose a candidate and a parsed JD, then click **Evaluate**. The result shows:

- a **0–100 score** and fit label (Strong / Moderate / Weak / *Not enough evidence to score*);
- a **verdict per requirement**: *met / partially met / not met / not enough evidence*, each citing the evidence behind it;
- required items that are **not met** and those that are **unproven** (no evidence; never counted as failed);
- strengths, gaps and discrepancies (resume claims contradicted by platform data);
- the evidence used, with trust labels: *Platform data* (GitHub/Codeforces), *Resume claim*, *Self-reported* (LeetCode);
- sources used, warnings, and a "decision support, not a hiring decision" notice.

Earlier evaluations are listed per candidate; **Re-run** uses the latest evidence.

---

## Phase 5 design (decisions confirmed by you, 2026-11-14)

| Decision | Choice |
|---|---|
| Result form | **Score + per-requirement verdicts.** The LLM gives verdicts with evidence; **the backend computes the number** |
| Privacy | **Identity removed before the LLM call**: name, e-mail, phone, links, platform usernames; contact details, location, institution, grades and GitHub bio are not sent at all |
| Scope | **Evaluation only.** Interview questions and recruiter reports are for later phases |

**Pipeline:**
1. **Evidence bundle (deterministic):** resume, GitHub, Codeforces and LeetCode facts, each with an id and trust level.
2. **Requirement matching (deterministic):** skills with aliases (Postgres = PostgreSQL), plus experience, education and qualifications. Duplicated qualifications are not counted twice.
3. **LLM verdicts:** strict schema, with a corrective retry.
4. **Backend guards:**
   - unknown evidence ids are removed;
   - "met" without evidence and "not met" without evidence both become *not enough evidence*;
   - a skill judged "not met" despite platform evidence is flagged for review.
5. **Score:** required items weigh 2 and preferred items 1. Evidence that is only self-reported counts ×0.75. If less than 50% of the requirement weight can be judged, the score is **null**, never a low number. A required item that is not met caps the label at Moderate.

---

## Phase 5: verified (2026-11-14)

**Real end-to-end** (running backend, your PostgreSQL, real Groq, real GitHub; synthetic candidate, deleted afterwards):

| Step | Result |
|---|---|
| Resume PDF upload → real Groq parse | completed in 3.1 s |
| JD upload → real Groq parse | completed in 4.6 s (4 required, 3 preferred skills) |
| GitHub `octocat` (real API) | collected |
| `POST /evaluations` | 202; defaulted to the latest parsed resume |
| Evaluation (real Groq `openai/gpt-oss-120b`) | **completed in 6.1 s**; score 95.7, Strong; Kubernetes → *not enough evidence* (not "not met") |
| Privacy | 0 identity values in the evidence sent to the LLM; 0 e-mails and no name in the backend log |
| JD deletion while referenced | 409 (evaluation history protected) |
| Cleanup | evaluation, JD file, resume file, GitHub link and candidate removed |

**Defects this run exposed, now fixed:**
- **Double-counting.** The JD parser can repeat skills, experience and education as qualifications, which counted them twice.
- **Unfaithful summary.** The model's summary said "meets all required skills" while a required skill had no evidence.
- **Hidden unproven skills.** A "Strong" label could hide an unproven required skill.
- **Score type.** The API returned the score as a string.
- **Lost results.** A failed corrective retry discarded a usable first answer.

---

## LeetCode integration type (Phase 4)

| Type | Applies? |
|---|---|
| Official API based | ❌ No official public API |
| Authorized integration based | ❌ No developer program / OAuth |
| **Manual candidate-submitted evidence** | ✅ **Yes, labelled self-reported and not verified** |
| Automated collection | Unavailable: the [Terms](https://leetcode.com/terms/) forbid scraping, and [robots.txt](https://leetcode.com/robots.txt) disallows `/graphql` and `/api/` |

---

## At a glance

| Area | Status |
|---|---|
| Backend (FastAPI, Python 3.12) | ✅ Health, candidates, resumes, JDs, GitHub, Codeforces, LeetCode (manual), **evaluations** |
| Database (PostgreSQL 18, local) | ✅ `candidate_intel` at **`0006_evaluation_errors` (head)**, applied 2026-11-14, no drift |
| LLM (Groq) | ✅ `openai/gpt-oss-120b` (the default changed after Groq retired `llama-3.3-70b-versatile` for this key) |
| Frontend (Next.js 16) | ✅ `/resumes`, `/job-descriptions`, `/candidates`, **`/evaluations`** |
| Automated tests | ✅ **1,410 passing**: backend 1,073 unit + 122 live, frontend 215. Real Groq 3/3 (resume, JD, evaluation) |
| Docker | ⚪ Not used (by decision) |

---

## Test results (2026-11-14)

| Suite | Result |
|---|---|
| Backend lint / format / types | ✅ ruff clean (145 files); mypy strict: 0 issues in 144 files |
| Backend unit | ✅ **1,073 passed** (96 new evaluation tests) |
| Backend live (PostgreSQL) | ✅ **122 passed**, 7 skipped (opt-in real GitHub, Codeforces, Groq) |
| Real Groq (opt-in) | ✅ **3/3**: resume structuring, JD structuring, **candidate evaluation** |
| Backend coverage | ✅ **96%** (evaluation modules 91–100%) |
| Frontend lint / typecheck / build | ✅ routes include `/evaluations` |
| Frontend tests | ✅ **215 passed** (14 new) |

Details: [TEST_REPORT.md](TEST_REPORT.md).

---

## Open items

| # | Item | Impact | Action |
|---|---|---|---|
| 1 | **`ENABLED_PLATFORMS` is empty in your `backend/.env`** | Platform features are disabled in normal runs (evaluations still work with resumes) | Set `ENABLED_PLATFORMS=github,codeforces,leetcode` |
| 2 | **`frontend/src/app/resumes/page.tsx` is missing from the pushed commit** (`.gitignore` fixed in Phase 3) | A fresh clone has no `/resumes` page | Include it and `/candidates` in the next commit |
| 3 | **Groq free tier: 8,000 tokens per minute** | Large evaluations previously failed with HTTP 413 (fixed: the prompt is fitted to `LLM_EVALUATION_MAX_PROMPT_TOKENS`=4,500 and less relevant evidence is marked "not sent"); several evaluations in one minute → `llm_rate_limited` | Wait a minute and **Re-run**; on a paid tier raise the budget for fuller prompts |
| 4 | LLM verdicts and summary can still be imperfect | They support a decision, not make it | Verdicts cite evidence; guards fix unsupported verdicts; the summary is labelled as model-written |
| 5 | Experience is estimated from free-text resume dates | Unparseable dates mean "unknown" | Reported per evaluation |
| 6 | No resume picker in the UI (API accepts `resume_id`) | Uses the latest parsed resume | Later UI improvement |
| 7 | Ownership of platform accounts is not verified | Evidence is attributed, not proven | Later phase |
| 8 | In-process background tasks; no authentication | As in earlier phases | Later phases |
| 9 | Phases 2–5 not committed yet | | Commit and push when you are ready (as NiranjanKJ304) |

---

## How to run

```powershell
cd backend
uv run alembic upgrade head                 # 0006_evaluation_errors
uv run uvicorn app.main:create_app --factory --reload --port 8000

cd frontend
npm run dev                                  # http://localhost:3000/evaluations
```

---

[README.md](README.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [API_DOCUMENTATION.md](API_DOCUMENTATION.md) ·
[DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md) · [TEST_REPORT.md](TEST_REPORT.md)
