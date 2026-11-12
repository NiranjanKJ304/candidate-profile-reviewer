/** Synthetic job-description payloads for frontend tests (fictional company). */

export const JD_ID = "j0000000-0000-4000-8000-000000000001";

export const PARSED_JD = {
  job_title: "Senior Backend Engineer",
  company_name: "Example Analytics Ltd",
  location: "Springfield (Hybrid)",
  job_summary: "Build data APIs.",
  required_skills: [
    { name: "Python", category: "language", evidence: "Strong experience with Python and FastAPI" },
    { name: "PostgreSQL", category: "database", evidence: "Experience with PostgreSQL" },
  ],
  preferred_skills: [{ name: "Docker", category: "devops", evidence: "Experience with Docker and Kubernetes" }],
  minimum_experience: {
    years_min: 5,
    years_max: null,
    description: "Professional software development",
    evidence: "5+ years of professional software development experience",
  },
  preferred_experience: null,
  education_requirements: [
    {
      level: "bachelor",
      field_of_study: "Computer Science",
      mandatory: true,
      description: null,
      evidence: "Bachelor's degree in Computer Science",
    },
  ],
  responsibilities: [{ text: "Design and build REST APIs in Python", evidence: "Design and build REST APIs in Python" }],
  required_qualifications: [],
  soft_skills: [{ text: "Communication", evidence: "Excellent communication skills" }],
  seniority: { level: "senior", evidence: "Senior Backend Engineer" },
};

export const TECH = {
  programming_languages: ["Python"],
  frameworks_and_libraries: [],
  databases: ["PostgreSQL"],
  cloud_and_devops: ["Docker"],
  tools: [],
  other: [],
};

const base = {
  id: JD_ID,
  title: null as string | null,
  company: null as string | null,
  original_filename: "role.txt",
  content_type: "text/plain; charset=utf-8",
  file_size_bytes: 900,
  parse_status: "pending",
  parse_error_code: null as string | null,
  extraction_method: null as string | null,
  has_review: false,
  created_at: "2026-11-12T10:00:00Z",
  updated_at: "2026-11-12T10:00:00Z",
};

export const SUMMARY_PENDING = { ...base };

export function jdDetail(overrides: Record<string, unknown> = {}) {
  return {
    ...base,
    sha256: "cd".repeat(32),
    page_count: null,
    parse_error: null,
    parse_warnings: [],
    parser_model: null,
    parsed_at: null,
    reviewed_at: null,
    text_char_count: null,
    ...overrides,
  };
}

export const DETAIL_COMPLETED = jdDetail({
  parse_status: "completed",
  title: "Senior Backend Engineer",
  company: "Example Analytics Ltd",
  extraction_method: "txt",
  text_char_count: 870,
  parser_model: "llama-3.3-70b-versatile",
});

export const DETAIL_FAILED = jdDetail({
  parse_status: "failed",
  extraction_method: "txt",
  parse_error_code: "llm_timeout",
  parse_error: "LLM did not respond within 60s",
  text_char_count: 870,
});

export function parsedResponse(overrides: Record<string, unknown> = {}) {
  return {
    jd_id: JD_ID,
    source: "extracted",
    data: PARSED_JD,
    extracted: PARSED_JD,
    technology_summary: TECH,
    parser_model: "llama-3.3-70b-versatile",
    parsed_at: "2026-11-12T10:00:05Z",
    reviewed_at: null,
    warnings: ["Removed required skill(s) not found in the job description: Rust"],
    ...overrides,
  };
}

export function page(items: object[]) {
  return { items, total: items.length, limit: 50, offset: 0 };
}
