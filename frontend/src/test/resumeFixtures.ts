/** Synthetic API payloads for frontend tests (fictional person, no real data). */

export const CAPS = {
  llm: { configured: true, provider: "groq", model: "llama-3.3-70b-versatile" },
  ocr: { enabled: true, available: true, language: "eng" },
  resume: { max_bytes: 10 * 1024 * 1024, max_pages: 30, accepted_extensions: [".pdf", ".docx"] },
};

export const CANDIDATE = { id: "c0000000-0000-4000-8000-000000000001", full_name: "Jordan Sample", email: null };
export const RID = "r0000000-0000-4000-8000-000000000001";

const base = {
  id: RID,
  candidate_id: CANDIDATE.id,
  original_filename: "jordan.pdf",
  content_type: "application/pdf",
  file_size_bytes: 2048,
  sha256: "ab".repeat(32),
  parse_error_code: null,
  parse_error: null,
  extraction_method: null,
  page_count: 1,
  parsed_at: null,
  created_at: "2026-11-12T10:00:00Z",
  updated_at: "2026-11-12T10:00:00Z",
};

export const SUMMARY_PENDING = { ...base, parse_status: "pending" };

export const PARSED = {
  personal_details: {
    full_name: "Jordan Sample",
    email: "jordan.sample@example.org",
    phone: null,
    location: "Springfield",
    linkedin_url: null,
    github_url: "github.com/jordan-sample",
    portfolio_url: null,
    other_links: [],
    summary: null,
  },
  education: [
    {
      institution: "Example University",
      degree: "B.Sc.",
      field_of_study: "Computer Science",
      start_date: "2017",
      end_date: "2021",
      grade: null,
      location: null,
    },
  ],
  work_experience: [
    {
      company: "Example Corp",
      title: "Software Engineer",
      location: null,
      start_date: "Jan 2021",
      end_date: "Present",
      is_current: true,
      responsibilities: ["Built REST APIs"],
      technologies: ["Python"],
    },
  ],
  internships: [],
  technical_skills: [
    { name: "Python", category: "language" },
    { name: "FastAPI", category: "framework" },
  ],
  projects: [],
  certifications: [],
  achievements: [{ description: "Winner, Example Hackathon 2020", date: "2020" }],
};

export function detail(overrides: Record<string, unknown> = {}) {
  return {
    ...base,
    parse_status: "pending",
    parsed_data: null,
    parse_warnings: [],
    parser_model: null,
    text_char_count: null,
    ...overrides,
  };
}

export const DETAIL_COMPLETED = detail({
  parse_status: "completed",
  extraction_method: "pdf_text",
  parsed_data: PARSED,
  parser_model: "llama-3.3-70b-versatile",
  text_char_count: 513,
  parse_warnings: ["Removed skill(s) not found in the resume text: Rust"],
});

export const DETAIL_FAILED = detail({
  parse_status: "failed",
  extraction_method: "pdf_text",
  parse_error_code: "llm_not_configured",
  parse_error: "No LLM API key is configured",
  text_char_count: 513,
});
