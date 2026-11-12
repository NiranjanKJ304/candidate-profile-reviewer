/**
 * Resume API: types, runtime parsers and calls.
 *
 * Every response is validated before use. Optional values stay `null` and empty
 * sections stay `[]` - the UI shows "Not found in resume", never a guess.
 */

import { type ApiClient, type FetchResult, UPLOAD_TIMEOUT_MS } from "@/lib/apiClient";

// --------------------------------------------------------------------------- types
export type ParseStatus = "pending" | "processing" | "completed" | "failed";
export type ExtractionMethod = "pdf_text" | "pdf_ocr" | "docx";

export interface Candidate {
  id: string;
  full_name: string;
  email: string | null;
}

export interface ResumeSummary {
  id: string;
  candidate_id: string;
  original_filename: string;
  content_type: string;
  file_size_bytes: number;
  parse_status: ParseStatus;
  parse_error_code: string | null;
  parse_error: string | null;
  extraction_method: ExtractionMethod | null;
  page_count: number | null;
  created_at: string;
}

type Text = string | null;

export interface PersonalDetails {
  full_name: Text;
  email: Text;
  phone: Text;
  location: Text;
  linkedin_url: Text;
  github_url: Text;
  portfolio_url: Text;
  other_links: string[];
  summary: Text;
}

export interface Education {
  institution: Text;
  degree: Text;
  field_of_study: Text;
  start_date: Text;
  end_date: Text;
  grade: Text;
  location: Text;
}

export interface Role {
  company: Text;
  title: Text;
  location: Text;
  start_date: Text;
  end_date: Text;
  is_current: boolean | null;
  responsibilities: string[];
  technologies: string[];
}

export interface Skill {
  name: string;
  category: Text;
}

export interface Project {
  name: Text;
  description: Text;
  role: Text;
  technologies: string[];
  url: Text;
  start_date: Text;
  end_date: Text;
}

export interface Certification {
  name: Text;
  issuer: Text;
  issue_date: Text;
  expiry_date: Text;
  credential_id: Text;
  url: Text;
}

export interface Achievement {
  description: string;
  date: Text;
}

export interface ParsedResume {
  personal_details: PersonalDetails;
  education: Education[];
  work_experience: Role[];
  internships: Role[];
  technical_skills: Skill[];
  projects: Project[];
  certifications: Certification[];
  achievements: Achievement[];
}

export interface ResumeDetail extends ResumeSummary {
  parsed_data: ParsedResume | null;
  parse_warnings: string[];
  parser_model: string | null;
  text_char_count: number | null;
}

export interface Capabilities {
  llm: { configured: boolean; provider: string; model: string };
  ocr: { enabled: boolean; available: boolean; language: string };
  resume: { max_bytes: number; max_pages: number; accepted_extensions: string[] };
  /** Present on servers with job-description support (Phase 1B+). */
  job_description: { max_bytes: number; max_pages: number; accepted_extensions: string[] } | null;
}

// --------------------------------------------------------------------------- parsers
class Invalid extends Error {}

function rec(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Invalid();
  return value as Record<string, unknown>;
}
function str(value: unknown): string {
  if (typeof value !== "string") throw new Invalid();
  return value;
}
function optStr(value: unknown): string | null {
  return value === null || value === undefined ? null : str(value);
}
function num(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new Invalid();
  return value;
}
function optNum(value: unknown): number | null {
  return value === null || value === undefined ? null : num(value);
}
function optBool(value: unknown): boolean | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "boolean") throw new Invalid();
  return value;
}
function list<T>(value: unknown, item: (v: unknown) => T): T[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) throw new Invalid();
  return value.map(item);
}
function oneOf<T extends string>(value: unknown, allowed: readonly T[]): T {
  const s = str(value);
  if (!(allowed as readonly string[]).includes(s)) throw new Invalid();
  return s as T;
}
function safe<T>(fn: () => T): T | null {
  try {
    return fn();
  } catch (err) {
    if (err instanceof Invalid) return null;
    throw err;
  }
}

const STATUSES = ["pending", "processing", "completed", "failed"] as const;
const METHODS = ["pdf_text", "pdf_ocr", "docx"] as const;

function role(v: unknown): Role {
  const r = rec(v);
  return {
    company: optStr(r.company),
    title: optStr(r.title),
    location: optStr(r.location),
    start_date: optStr(r.start_date),
    end_date: optStr(r.end_date),
    is_current: optBool(r.is_current),
    responsibilities: list(r.responsibilities, str),
    technologies: list(r.technologies, str),
  };
}

function parsedResume(v: unknown): ParsedResume {
  const r = rec(v);
  const pd = rec(r.personal_details ?? {});
  return {
    personal_details: {
      full_name: optStr(pd.full_name),
      email: optStr(pd.email),
      phone: optStr(pd.phone),
      location: optStr(pd.location),
      linkedin_url: optStr(pd.linkedin_url),
      github_url: optStr(pd.github_url),
      portfolio_url: optStr(pd.portfolio_url),
      other_links: list(pd.other_links, str),
      summary: optStr(pd.summary),
    },
    education: list(r.education, (e) => {
      const x = rec(e);
      return {
        institution: optStr(x.institution),
        degree: optStr(x.degree),
        field_of_study: optStr(x.field_of_study),
        start_date: optStr(x.start_date),
        end_date: optStr(x.end_date),
        grade: optStr(x.grade),
        location: optStr(x.location),
      };
    }),
    work_experience: list(r.work_experience, role),
    internships: list(r.internships, role),
    technical_skills: list(r.technical_skills, (s) => {
      const x = rec(s);
      return { name: str(x.name), category: optStr(x.category) };
    }),
    projects: list(r.projects, (p) => {
      const x = rec(p);
      return {
        name: optStr(x.name),
        description: optStr(x.description),
        role: optStr(x.role),
        technologies: list(x.technologies, str),
        url: optStr(x.url),
        start_date: optStr(x.start_date),
        end_date: optStr(x.end_date),
      };
    }),
    certifications: list(r.certifications, (c) => {
      const x = rec(c);
      return {
        name: optStr(x.name),
        issuer: optStr(x.issuer),
        issue_date: optStr(x.issue_date),
        expiry_date: optStr(x.expiry_date),
        credential_id: optStr(x.credential_id),
        url: optStr(x.url),
      };
    }),
    achievements: list(r.achievements, (a) => {
      const x = rec(a);
      return { description: str(x.description), date: optStr(x.date) };
    }),
  };
}

function summary(v: unknown): ResumeSummary {
  const r = rec(v);
  return {
    id: str(r.id),
    candidate_id: str(r.candidate_id),
    original_filename: str(r.original_filename),
    content_type: str(r.content_type),
    file_size_bytes: num(r.file_size_bytes),
    parse_status: oneOf(r.parse_status, STATUSES),
    parse_error_code: optStr(r.parse_error_code),
    parse_error: optStr(r.parse_error),
    extraction_method: r.extraction_method == null ? null : oneOf(r.extraction_method, METHODS),
    page_count: optNum(r.page_count),
    created_at: str(r.created_at),
  };
}

export const parseCandidate = (v: unknown): Candidate | null =>
  safe(() => {
    const r = rec(v);
    return { id: str(r.id), full_name: str(r.full_name), email: optStr(r.email) };
  });

export const parseResumeSummary = (v: unknown): ResumeSummary | null => safe(() => summary(v));

export const parseResumeDetail = (v: unknown): ResumeDetail | null =>
  safe(() => {
    const r = rec(v);
    return {
      ...summary(r),
      parsed_data: r.parsed_data == null ? null : parsedResume(r.parsed_data),
      parse_warnings: list(r.parse_warnings, str),
      parser_model: optStr(r.parser_model),
      text_char_count: optNum(r.text_char_count),
    };
  });

export const parseCapabilities = (v: unknown): Capabilities | null =>
  safe(() => {
    const r = rec(v);
    const llm = rec(r.llm);
    const ocr = rec(r.ocr);
    const resume = rec(r.resume);
    const limits = (x: Record<string, unknown>) => ({
      max_bytes: num(x.max_bytes),
      max_pages: num(x.max_pages),
      accepted_extensions: list(x.accepted_extensions, str),
    });
    const bool = (x: unknown) => {
      const b = optBool(x);
      if (b === null) throw new Invalid();
      return b;
    };
    return {
      llm: { configured: bool(llm.configured), provider: str(llm.provider), model: str(llm.model) },
      ocr: { enabled: bool(ocr.enabled), available: bool(ocr.available), language: str(ocr.language) },
      resume: limits(resume),
      job_description: r.job_description == null ? null : limits(rec(r.job_description)),
    };
  });

// --------------------------------------------------------------------------- calls
export function getCapabilities(api: ApiClient): Promise<FetchResult<Capabilities>> {
  return api.get("/system/capabilities", parseCapabilities);
}

export function createCandidate(
  api: ApiClient,
  input: { full_name: string; email?: string | null },
): Promise<FetchResult<Candidate>> {
  const body: Record<string, string> = { full_name: input.full_name };
  if (input.email) body.email = input.email;
  return api.post("/candidates", body, parseCandidate);
}

export function uploadResume(api: ApiClient, candidateId: string, file: File): Promise<FetchResult<ResumeSummary>> {
  const form = new FormData();
  form.append("file", file, file.name);
  return api.post(`/candidates/${encodeURIComponent(candidateId)}/resumes`, form, parseResumeSummary, {
    timeoutMs: UPLOAD_TIMEOUT_MS,
  });
}

export function getResume(api: ApiClient, resumeId: string): Promise<FetchResult<ResumeDetail>> {
  return api.get(`/resumes/${encodeURIComponent(resumeId)}`, parseResumeDetail);
}

export function reparseResume(api: ApiClient, resumeId: string): Promise<FetchResult<ResumeSummary>> {
  return api.post(`/resumes/${encodeURIComponent(resumeId)}/parse`, undefined, parseResumeSummary);
}

export function deleteResume(api: ApiClient, resumeId: string): Promise<FetchResult<null>> {
  return api.delete(`/resumes/${encodeURIComponent(resumeId)}`);
}

export function resumeFileUrl(api: ApiClient, resumeId: string): string {
  return `${api.baseUrl.replace(/\/+$/, "")}/api/v1/resumes/${encodeURIComponent(resumeId)}/file`;
}

// --------------------------------------------------------------------------- client-side checks
export function checkFile(file: File, maxBytes: number): string | null {
  const name = file.name.toLowerCase();
  if (!name.endsWith(".pdf") && !name.endsWith(".docx")) return "Only PDF (.pdf) and Word (.docx) files are accepted.";
  if (file.size === 0) return "The selected file is empty.";
  if (file.size > maxBytes) return `The file is larger than the ${Math.floor(maxBytes / (1024 * 1024))} MB limit.`;
  return null;
}
