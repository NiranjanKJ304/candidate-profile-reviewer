"use client";

import { useState } from "react";

import {
  type ExperienceRequirement,
  type ParsedJobDescription,
  SENIORITY_LEVELS,
  type SeniorityLevel,
  linesToSkills,
  linesToTexts,
  skillsToLines,
  textsToLines,
} from "@/lib/jobDescriptions";

interface Props {
  initial: ParsedJobDescription;
  saving: boolean;
  onSave: (data: ParsedJobDescription) => void;
  onCancel: () => void;
}

function toNumber(value: string): number | null {
  if (!value.trim()) return null;
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 && n <= 50 ? n : NaN;
}

function exp(prev: ExperienceRequirement | null, min: number | null, max: number | null): ExperienceRequirement | null {
  if (min === null && max === null && !prev?.description) return null;
  const unchanged = prev && prev.years_min === min && prev.years_max === max;
  return {
    years_min: min,
    years_max: max,
    description: prev?.description ?? null,
    evidence: unchanged ? prev.evidence : null, // edited values are no longer backed by the quote
  };
}

const inputCls = "rounded-md border border-zinc-300 px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-950";

export default function JDReviewEditor({ initial, saving, onSave, onCancel }: Props) {
  const [title, setTitle] = useState(initial.job_title ?? "");
  const [company, setCompany] = useState(initial.company_name ?? "");
  const [location, setLocation] = useState(initial.location ?? "");
  const [seniority, setSeniority] = useState<string>(initial.seniority?.level ?? "");
  const [required, setRequired] = useState(skillsToLines(initial.required_skills));
  const [preferred, setPreferred] = useState(skillsToLines(initial.preferred_skills));
  const [minYears, setMinYears] = useState(initial.minimum_experience?.years_min?.toString() ?? "");
  const [maxYears, setMaxYears] = useState(initial.minimum_experience?.years_max?.toString() ?? "");
  const [prefYears, setPrefYears] = useState(initial.preferred_experience?.years_min?.toString() ?? "");
  const [responsibilities, setResponsibilities] = useState(textsToLines(initial.responsibilities));
  const [qualifications, setQualifications] = useState(textsToLines(initial.required_qualifications));
  const [softSkills, setSoftSkills] = useState(textsToLines(initial.soft_skills));
  const [error, setError] = useState<string | null>(null);

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const min = toNumber(minYears);
    const max = toNumber(maxYears);
    const pref = toNumber(prefYears);
    if ([min, max, pref].some((v) => Number.isNaN(v))) return setError("Years must be numbers between 0 and 50.");
    if (min !== null && max !== null && max < min) return setError("Maximum years cannot be below minimum years.");
    const requiredSkills = linesToSkills(required, initial.required_skills);
    const requiredNames = new Set(requiredSkills.map((s) => s.name.toLowerCase()));
    const preferredSkills = linesToSkills(preferred, initial.preferred_skills).filter(
      (s) => !requiredNames.has(s.name.toLowerCase()),
    );
    setError(null);
    onSave({
      ...initial,
      job_title: title.trim() || null,
      company_name: company.trim() || null,
      location: location.trim() || null,
      seniority: seniority
        ? {
            level: seniority as SeniorityLevel,
            evidence: seniority === initial.seniority?.level ? initial.seniority.evidence : null,
          }
        : null,
      required_skills: requiredSkills,
      preferred_skills: preferredSkills,
      minimum_experience: exp(initial.minimum_experience, min, max),
      preferred_experience: exp(initial.preferred_experience, pref, initial.preferred_experience?.years_max ?? null),
      responsibilities: linesToTexts(responsibilities, initial.responsibilities),
      required_qualifications: linesToTexts(qualifications, initial.required_qualifications),
      soft_skills: linesToTexts(softSkills, initial.soft_skills),
    });
  }

  const field = (label: string, value: string, set: (v: string) => void) => (
    <label className="grid gap-1 text-sm font-medium">
      {label}
      <input value={value} onChange={(e) => set(e.target.value)} className={inputCls} maxLength={300} />
    </label>
  );
  const area = (label: string, hint: string, value: string, set: (v: string) => void) => (
    <label className="grid gap-1 text-sm font-medium">
      {label}
      <span className="text-xs font-normal text-zinc-500">{hint}</span>
      <textarea value={value} onChange={(e) => set(e.target.value)} rows={5} className={`${inputCls} font-mono`} />
    </label>
  );

  return (
    <form
      onSubmit={submit}
      aria-label="Review extracted job information"
      className="grid gap-4 rounded-xl border border-indigo-200 bg-white p-5 dark:border-indigo-900 dark:bg-zinc-900"
    >
      <p className="text-sm text-zinc-600 dark:text-zinc-400">
        Correct the extracted information before it is used for evaluation. The original extraction is kept and can be
        restored at any time.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        {field("Job title", title, setTitle)}
        {field("Company", company, setCompany)}
        {field("Location", location, setLocation)}
        <label className="grid gap-1 text-sm font-medium">
          Seniority
          <select value={seniority} onChange={(e) => setSeniority(e.target.value)} className={inputCls}>
            <option value="">Not stated</option>
            {SENIORITY_LEVELS.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {area("Required skills", "One per line: Name | category", required, setRequired)}
        {area("Preferred skills", "One per line: Name | category", preferred, setPreferred)}
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        {field("Minimum years", minYears, setMinYears)}
        {field("Maximum years", maxYears, setMaxYears)}
        {field("Preferred years", prefYears, setPrefYears)}
      </div>
      {area("Responsibilities", "One per line", responsibilities, setResponsibilities)}
      <div className="grid gap-3 sm:grid-cols-2">
        {area("Required qualifications", "One per line", qualifications, setQualifications)}
        {area("Soft skills", "One per line", softSkills, setSoftSkills)}
      </div>
      {error && (
        <p role="alert" className="text-sm text-red-700 dark:text-red-400">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={saving}
          className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-60"
        >
          {saving ? "Saving…" : "Save corrections"}
        </button>
        <button type="button" onClick={onCancel} className="rounded-md px-4 py-2 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800">
          Cancel
        </button>
      </div>
    </form>
  );
}
