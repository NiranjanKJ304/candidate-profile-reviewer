/**
 * Job-description API: types, runtime parsers and calls.
 * Mirrors the backend schemas in app/schemas/job_description_*.py.
 */

import { type ApiClient, type FetchResult, UPLOAD_TIMEOUT_MS } from "@/lib/apiClient";
import {
  bool,
  list,
  num,
  oneOf,
  optBool,
  optNum,
  optOneOf,
  optStr,
  rec,
  safe,
  str,
} from "@/lib/parse";

export const SKILL_CATEGORIES = ["language", "framework", "library", "database", "cloud", "devops", "tool", "other"] as const;
export const SENIORITY_LEVELS = ["intern", "junior", "mid", "senior", "lead", "principal", "manager"] as const;
export const EDUCATION_LEVELS = ["high_school", "diploma", "associate", "bachelor", "master", "doctorate", "other"] as const;
const STATUSES = ["pending", "processing", "completed", "failed"] as const;
const METHODS = ["pdf_text", "pdf_ocr", "docx", "txt"] as const;

export type SkillCategory = (typeof SKILL_CATEGORIES)[number];
export type SeniorityLevel = (typeof SENIORITY_LEVELS)[number];
export type EducationLevel = (typeof EDUCATION_LEVELS)[number];
export type JDStatus = (typeof STATUSES)[number];

export interface SkillRequirement {
  name: string;
  category: SkillCategory | null;
  evidence: string | null;
}
export interface ExperienceRequirement {
  years_min: number | null;
  years_max: number | null;
  description: string | null;
  evidence: string | null;
}
export interface EducationRequirement {
  level: EducationLevel | null;
  field_of_study: string | null;
  mandatory: boolean | null;
  description: string | null;
  evidence: string | null;
}
export interface TextRequirement {
  text: string;
  evidence: string | null;
}
export interface ParsedJobDescription {
  job_title: string | null;
  company_name: string | null;
  location: string | null;
  job_summary: string | null;
  required_skills: SkillRequirement[];
  preferred_skills: SkillRequirement[];
  minimum_experience: ExperienceRequirement | null;
  preferred_experience: ExperienceRequirement | null;
  education_requirements: EducationRequirement[];
  responsibilities: TextRequirement[];
  required_qualifications: TextRequirement[];
  soft_skills: TextRequirement[];
  seniority: { level: SeniorityLevel | null; evidence: string | null } | null;
}
export interface TechnologySummary {
  programming_languages: string[];
  frameworks_and_libraries: string[];
  databases: string[];
  cloud_and_devops: string[];
  tools: string[];
  other: string[];
}
export interface JobDescriptionSummary {
  id: string;
  title: string | null;
  company: string | null;
  original_filename: string | null;
  file_size_bytes: number | null;
  parse_status: JDStatus;
  parse_error_code: string | null;
  extraction_method: (typeof METHODS)[number] | null;
  has_review: boolean;
  created_at: string;
}
export interface JobDescriptionDetail extends JobDescriptionSummary {
  page_count: number | null;
  parse_error: string | null;
  parse_warnings: string[];
  parser_model: string | null;
  text_char_count: number | null;
}
export interface ParsedJDResponse {
  source: "extracted" | "reviewed";
  data: ParsedJobDescription;
  extracted: ParsedJobDescription;
  technology_summary: TechnologySummary;
  parser_model: string | null;
  warnings: string[];
}
export interface JDPage {
  items: JobDescriptionSummary[];
  total: number;
}

// --------------------------------------------------------------------------- parsers
const skill = (v: unknown): SkillRequirement => {
  const r = rec(v);
  return { name: str(r.name), category: optOneOf(r.category, SKILL_CATEGORIES), evidence: optStr(r.evidence) };
};
const experience = (v: unknown): ExperienceRequirement | null => {
  if (v === null || v === undefined) return null;
  const r = rec(v);
  return {
    years_min: optNum(r.years_min),
    years_max: optNum(r.years_max),
    description: optStr(r.description),
    evidence: optStr(r.evidence),
  };
};
const textReq = (v: unknown): TextRequirement => {
  const r = rec(v);
  return { text: str(r.text), evidence: optStr(r.evidence) };
};

export function parseParsedJD(v: unknown): ParsedJobDescription {
  const r = rec(v);
  const sen = r.seniority == null ? null : rec(r.seniority);
  return {
    job_title: optStr(r.job_title),
    company_name: optStr(r.company_name),
    location: optStr(r.location),
    job_summary: optStr(r.job_summary),
    required_skills: list(r.required_skills, skill),
    preferred_skills: list(r.preferred_skills, skill),
    minimum_experience: experience(r.minimum_experience),
    preferred_experience: experience(r.preferred_experience),
    education_requirements: list(r.education_requirements, (e) => {
      const x = rec(e);
      return {
        level: optOneOf(x.level, EDUCATION_LEVELS),
        field_of_study: optStr(x.field_of_study),
        mandatory: optBool(x.mandatory),
        description: optStr(x.description),
        evidence: optStr(x.evidence),
      };
    }),
    responsibilities: list(r.responsibilities, textReq),
    required_qualifications: list(r.required_qualifications, textReq),
    soft_skills: list(r.soft_skills, textReq),
    seniority: sen ? { level: optOneOf(sen.level, SENIORITY_LEVELS), evidence: optStr(sen.evidence) } : null,
  };
}

function summary(v: unknown): JobDescriptionSummary {
  const r = rec(v);
  return {
    id: str(r.id),
    title: optStr(r.title),
    company: optStr(r.company),
    original_filename: optStr(r.original_filename),
    file_size_bytes: optNum(r.file_size_bytes),
    parse_status: oneOf(r.parse_status, STATUSES),
    parse_error_code: optStr(r.parse_error_code),
    extraction_method: optOneOf(r.extraction_method, METHODS),
    has_review: bool(r.has_review),
    created_at: str(r.created_at),
  };
}

export const parseJDSummary = (v: unknown) => safe(() => summary(v));

export const parseJDDetail = (v: unknown): JobDescriptionDetail | null =>
  safe(() => {
    const r = rec(v);
    return {
      ...summary(r),
      page_count: optNum(r.page_count),
      parse_error: optStr(r.parse_error),
      parse_warnings: list(r.parse_warnings, str),
      parser_model: optStr(r.parser_model),
      text_char_count: optNum(r.text_char_count),
    };
  });

export const parseJDPage = (v: unknown): JDPage | null =>
  safe(() => {
    const r = rec(v);
    return { items: list(r.items, summary), total: num(r.total) };
  });

export const parseParsedResponse = (v: unknown): ParsedJDResponse | null =>
  safe(() => {
    const r = rec(v);
    const t = rec(r.technology_summary);
    return {
      source: oneOf(r.source, ["extracted", "reviewed"] as const),
      data: parseParsedJD(r.data),
      extracted: parseParsedJD(r.extracted),
      technology_summary: {
        programming_languages: list(t.programming_languages, str),
        frameworks_and_libraries: list(t.frameworks_and_libraries, str),
        databases: list(t.databases, str),
        cloud_and_devops: list(t.cloud_and_devops, str),
        tools: list(t.tools, str),
        other: list(t.other, str),
      },
      parser_model: optStr(r.parser_model),
      warnings: list(r.warnings, str),
    };
  });

// --------------------------------------------------------------------------- calls
const base = "/job-descriptions";
const id = (jdId: string) => `${base}/${encodeURIComponent(jdId)}`;

export function uploadJobDescription(api: ApiClient, file: File): Promise<FetchResult<JobDescriptionSummary>> {
  const form = new FormData();
  form.append("file", file, file.name);
  return api.post(`${base}/upload`, form, parseJDSummary, { timeoutMs: UPLOAD_TIMEOUT_MS });
}
export const listJobDescriptions = (api: ApiClient, limit = 50, offset = 0) =>
  api.get(`${base}?limit=${limit}&offset=${offset}`, parseJDPage);
export const getJobDescription = (api: ApiClient, jdId: string) => api.get(id(jdId), parseJDDetail);
export const getParsedJobDescription = (api: ApiClient, jdId: string) =>
  api.get(`${id(jdId)}/parsed`, parseParsedResponse);
export const saveReview = (api: ApiClient, jdId: string, data: ParsedJobDescription) =>
  api.put(`${id(jdId)}/parsed`, data, parseParsedResponse);
export const discardReview = (api: ApiClient, jdId: string) =>
  api.delete(`${id(jdId)}/parsed/review`);
export const reparseJobDescription = (api: ApiClient, jdId: string) =>
  api.post(`${id(jdId)}/parse`, undefined, parseJDSummary);
export const deleteJobDescription = (api: ApiClient, jdId: string) => api.delete(id(jdId));
export const jobDescriptionFileUrl = (api: ApiClient, jdId: string) =>
  `${api.baseUrl.replace(/\/+$/, "")}/api/v1${id(jdId)}/file`;

export function checkJDFile(file: File, maxBytes: number): string | null {
  const name = file.name.toLowerCase();
  if (![".pdf", ".docx", ".txt"].some((ext) => name.endsWith(ext))) {
    return "Only PDF (.pdf), Word (.docx) and text (.txt) files are accepted.";
  }
  if (file.size === 0) return "The selected file is empty.";
  if (file.size > maxBytes) return `The file is larger than the ${Math.floor(maxBytes / (1024 * 1024))} MB limit.`;
  return null;
}

// --------------------------------------------------------------------------- review editing
/** "Name | category" per line <-> skills; evidence is kept for unchanged names. */
export function skillsToLines(skills: SkillRequirement[]): string {
  return skills.map((s) => (s.category ? `${s.name} | ${s.category}` : s.name)).join("\n");
}
export function linesToSkills(text: string, previous: SkillRequirement[]): SkillRequirement[] {
  const byName = new Map(previous.map((s) => [s.name.toLowerCase(), s]));
  const seen = new Set<string>();
  const out: SkillRequirement[] = [];
  for (const raw of text.split("\n")) {
    const [namePart, catPart] = raw.split("|").map((p) => p.trim());
    if (!namePart || seen.has(namePart.toLowerCase())) continue;
    seen.add(namePart.toLowerCase());
    const category = (SKILL_CATEGORIES as readonly string[]).includes(catPart ?? "")
      ? (catPart as SkillCategory)
      : (byName.get(namePart.toLowerCase())?.category ?? null);
    out.push({ name: namePart, category, evidence: byName.get(namePart.toLowerCase())?.evidence ?? null });
  }
  return out;
}
export function textsToLines(items: TextRequirement[]): string {
  return items.map((i) => i.text).join("\n");
}
export function linesToTexts(text: string, previous: TextRequirement[]): TextRequirement[] {
  const byText = new Map(previous.map((i) => [i.text, i.evidence]));
  return text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((t) => ({ text: t, evidence: byText.get(t) ?? null }));
}
