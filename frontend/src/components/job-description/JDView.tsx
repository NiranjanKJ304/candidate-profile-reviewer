import type {
  ExperienceRequirement,
  ParsedJobDescription,
  SkillRequirement,
  TechnologySummary,
  TextRequirement,
} from "@/lib/jobDescriptions";

function NotStated({ what }: { what: string }) {
  return <p className="text-sm italic text-zinc-500">No {what} stated in the job description.</p>;
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  const id = `jd-${title.toLowerCase().replace(/[^a-z]+/g, "-")}`;
  return (
    <section aria-labelledby={id} className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
      <h3 id={id} className="text-sm font-semibold">
        {title}
      </h3>
      <div className="mt-3">{children}</div>
    </section>
  );
}

/** Verbatim JD quote supporting an extracted item. */
function Evidence({ quote }: { quote: string | null }) {
  if (!quote) return null;
  return <q className="block text-xs italic text-zinc-500">{quote}</q>;
}

function Skills({ skills, tone, what }: { skills: SkillRequirement[]; tone: "required" | "preferred"; what: string }) {
  if (skills.length === 0) return <NotStated what={what} />;
  const cls =
    tone === "required"
      ? "border-indigo-300 bg-indigo-50 text-indigo-900 dark:border-indigo-800 dark:bg-indigo-950 dark:text-indigo-200"
      : "border-zinc-300 bg-zinc-50 text-zinc-800 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200";
  return (
    <ul className="flex flex-wrap gap-2">
      {skills.map((s) => (
        <li
          key={s.name}
          className={`rounded-full border px-2.5 py-0.5 text-xs ${cls}`}
          title={[s.category, s.evidence ? `"${s.evidence}"` : null].filter(Boolean).join(" - ") || undefined}
        >
          {s.name}
        </li>
      ))}
    </ul>
  );
}

function years(e: ExperienceRequirement): string {
  if (e.years_min !== null && e.years_max !== null) return `${e.years_min}-${e.years_max} years`;
  if (e.years_min !== null) return `${e.years_min}+ years`;
  if (e.years_max !== null) return `up to ${e.years_max} years`;
  return "Years not stated";
}

function Experience({ label, value }: { label: string; value: ExperienceRequirement | null }) {
  return (
    <div>
      <p className="text-xs uppercase tracking-wide text-zinc-500">{label}</p>
      {value ? (
        <>
          <p className="text-sm font-medium">{years(value)}</p>
          {value.description && <p className="text-sm text-zinc-600 dark:text-zinc-400">{value.description}</p>}
          <Evidence quote={value.evidence} />
        </>
      ) : (
        <p className="text-sm italic text-zinc-500">Not stated</p>
      )}
    </div>
  );
}

function TextList({ items, what }: { items: TextRequirement[]; what: string }) {
  if (items.length === 0) return <NotStated what={what} />;
  return (
    <ul className="list-disc space-y-1.5 pl-5 text-sm">
      {items.map((i) => (
        <li key={i.text}>
          {i.text}
          {i.evidence && i.evidence !== i.text && <Evidence quote={i.evidence} />}
        </li>
      ))}
    </ul>
  );
}

const TECH_LABELS: [keyof TechnologySummary, string][] = [
  ["programming_languages", "Programming languages"],
  ["frameworks_and_libraries", "Frameworks & libraries"],
  ["databases", "Databases"],
  ["cloud_and_devops", "Cloud & DevOps"],
  ["tools", "Tools"],
  ["other", "Other"],
];

export default function JDView({ data, tech }: { data: ParsedJobDescription; tech: TechnologySummary }) {
  return (
    <div className="grid gap-4" data-testid="parsed-jd">
      <Card title="Role">
        <dl className="grid gap-3 sm:grid-cols-2">
          {(
            [
              ["Job title", data.job_title],
              ["Company", data.company_name],
              ["Location", data.location],
              ["Seniority", data.seniority?.level ?? null],
            ] as const
          ).map(([label, value]) => (
            <div key={label}>
              <dt className="text-xs uppercase tracking-wide text-zinc-500">{label}</dt>
              <dd className="text-sm">{value ?? <span className="italic text-zinc-400">Not stated</span>}</dd>
            </div>
          ))}
        </dl>
        {data.job_summary && <p className="mt-3 text-sm text-zinc-700 dark:text-zinc-300">{data.job_summary}</p>}
      </Card>

      <div className="grid gap-4 md:grid-cols-2">
        <Card title={`Required skills (${data.required_skills.length})`}>
          <Skills skills={data.required_skills} tone="required" what="required skills" />
        </Card>
        <Card title={`Preferred skills (${data.preferred_skills.length})`}>
          <Skills skills={data.preferred_skills} tone="preferred" what="preferred skills" />
        </Card>
      </div>

      <Card title="Technologies by category">
        <dl className="grid gap-2 sm:grid-cols-2">
          {TECH_LABELS.filter(([key]) => tech[key].length > 0).map(([key, label]) => (
            <div key={key}>
              <dt className="text-xs uppercase tracking-wide text-zinc-500">{label}</dt>
              <dd className="text-sm">{tech[key].join(", ")}</dd>
            </div>
          ))}
        </dl>
        {TECH_LABELS.every(([key]) => tech[key].length === 0) && <NotStated what="technologies" />}
      </Card>

      <Card title="Experience">
        <div className="grid gap-4 sm:grid-cols-2">
          <Experience label="Minimum" value={data.minimum_experience} />
          <Experience label="Preferred" value={data.preferred_experience} />
        </div>
      </Card>

      <Card title="Education">
        {data.education_requirements.length === 0 ? (
          <NotStated what="education requirements" />
        ) : (
          <ul className="space-y-2 text-sm">
            {data.education_requirements.map((e, i) => (
              <li key={i}>
                <span className="font-medium">{[e.level, e.field_of_study].filter(Boolean).join(", ") || "Education"}</span>
                {e.mandatory !== null && (
                  <span className="text-zinc-500"> · {e.mandatory ? "required" : "preferred"}</span>
                )}
                <Evidence quote={e.evidence} />
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Responsibilities">
        <TextList items={data.responsibilities} what="responsibilities" />
      </Card>
      <Card title="Required qualifications">
        <TextList items={data.required_qualifications} what="qualifications" />
      </Card>
      <Card title="Soft skills">
        <TextList items={data.soft_skills} what="soft skills" />
      </Card>
    </div>
  );
}
