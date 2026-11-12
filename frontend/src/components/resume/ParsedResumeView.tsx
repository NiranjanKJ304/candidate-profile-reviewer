import type { ParsedResume, Role } from "@/lib/resumes";

/** Shown wherever the resume did not state something: missing is not zero. */
function NotFound({ what }: { what: string }) {
  return <p className="text-sm italic text-zinc-500">No {what} found in the resume.</p>;
}

function Section({ title, count, children }: { title: string; count?: number; children: React.ReactNode }) {
  const id = `section-${title.toLowerCase().replace(/\s+/g, "-")}`;
  return (
    <section aria-labelledby={id} className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
      <h3 id={id} className="flex items-center gap-2 text-sm font-semibold">
        {title}
        {count !== undefined && (
          <span className="rounded-full bg-zinc-100 px-2 py-0.5 text-xs font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
            {count}
          </span>
        )}
      </h3>
      <div className="mt-3">{children}</div>
    </section>
  );
}

function dateRange(start: string | null, end: string | null): string | null {
  if (!start && !end) return null;
  return `${start ?? "?"} - ${end ?? "?"}`;
}

function Field({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-zinc-500">{label}</dt>
      <dd className="text-sm">{value ?? <span className="italic text-zinc-400">Not stated</span>}</dd>
    </div>
  );
}

function Roles({ roles, what }: { roles: Role[]; what: string }) {
  if (roles.length === 0) return <NotFound what={what} />;
  return (
    <ul className="space-y-3">
      {roles.map((r, i) => (
        <li key={i} className="text-sm">
          <p className="font-medium">
            {r.title ?? "Untitled role"}
            {r.company && <span className="font-normal text-zinc-600 dark:text-zinc-400"> · {r.company}</span>}
          </p>
          {(dateRange(r.start_date, r.end_date) || r.location) && (
            <p className="text-xs text-zinc-500">
              {[dateRange(r.start_date, r.end_date), r.location].filter(Boolean).join(" · ")}
            </p>
          )}
          {r.responsibilities.length > 0 && (
            <ul className="mt-1 list-disc space-y-0.5 pl-5 text-zinc-700 dark:text-zinc-300">
              {r.responsibilities.map((line, j) => (
                <li key={j}>{line}</li>
              ))}
            </ul>
          )}
          {r.technologies.length > 0 && (
            <p className="mt-1 text-xs text-zinc-500">Technologies: {r.technologies.join(", ")}</p>
          )}
        </li>
      ))}
    </ul>
  );
}

export default function ParsedResumeView({ data }: { data: ParsedResume }) {
  const pd = data.personal_details;
  const links = [pd.linkedin_url, pd.github_url, pd.portfolio_url, ...pd.other_links].filter(
    (l): l is string => Boolean(l),
  );
  return (
    <div className="grid gap-4" data-testid="parsed-resume">
      <Section title="Personal details">
        <dl className="grid gap-3 sm:grid-cols-2">
          <Field label="Name" value={pd.full_name} />
          <Field label="Email" value={pd.email} />
          <Field label="Phone" value={pd.phone} />
          <Field label="Location" value={pd.location} />
        </dl>
        {links.length > 0 && (
          <ul className="mt-3 flex flex-wrap gap-2 text-xs">
            {links.map((link) => (
              <li key={link} className="rounded bg-zinc-100 px-2 py-1 break-all dark:bg-zinc-800">
                {link}
              </li>
            ))}
          </ul>
        )}
        {pd.summary && <p className="mt-3 text-sm text-zinc-700 dark:text-zinc-300">{pd.summary}</p>}
      </Section>

      <Section title="Technical skills" count={data.technical_skills.length}>
        {data.technical_skills.length === 0 ? (
          <NotFound what="technical skills" />
        ) : (
          <ul className="flex flex-wrap gap-2">
            {data.technical_skills.map((s) => (
              <li
                key={s.name}
                className="rounded-full border border-indigo-200 bg-indigo-50 px-2.5 py-0.5 text-xs text-indigo-800 dark:border-indigo-900 dark:bg-indigo-950 dark:text-indigo-200"
                title={s.category ?? undefined}
              >
                {s.name}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Work experience" count={data.work_experience.length}>
        <Roles roles={data.work_experience} what="work experience" />
      </Section>

      <Section title="Internships" count={data.internships.length}>
        <Roles roles={data.internships} what="internships" />
      </Section>

      <Section title="Education" count={data.education.length}>
        {data.education.length === 0 ? (
          <NotFound what="education" />
        ) : (
          <ul className="space-y-2 text-sm">
            {data.education.map((e, i) => (
              <li key={i}>
                <p className="font-medium">
                  {[e.degree, e.field_of_study].filter(Boolean).join(", ") || "Qualification not stated"}
                </p>
                <p className="text-xs text-zinc-500">
                  {[e.institution, dateRange(e.start_date, e.end_date), e.grade].filter(Boolean).join(" · ")}
                </p>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Projects" count={data.projects.length}>
        {data.projects.length === 0 ? (
          <NotFound what="projects" />
        ) : (
          <ul className="space-y-2 text-sm">
            {data.projects.map((p, i) => (
              <li key={i}>
                <p className="font-medium">{p.name ?? "Untitled project"}</p>
                {p.description && <p className="text-zinc-700 dark:text-zinc-300">{p.description}</p>}
                {p.technologies.length > 0 && (
                  <p className="text-xs text-zinc-500">Technologies: {p.technologies.join(", ")}</p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Certifications" count={data.certifications.length}>
        {data.certifications.length === 0 ? (
          <NotFound what="certifications" />
        ) : (
          <ul className="space-y-1 text-sm">
            {data.certifications.map((c, i) => (
              <li key={i}>
                <span className="font-medium">{c.name ?? "Unnamed certification"}</span>
                {(c.issuer || c.issue_date) && (
                  <span className="text-zinc-500"> · {[c.issuer, c.issue_date].filter(Boolean).join(", ")}</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Achievements" count={data.achievements.length}>
        {data.achievements.length === 0 ? (
          <NotFound what="achievements" />
        ) : (
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {data.achievements.map((a, i) => (
              <li key={i}>
                {a.description}
                {a.date && <span className="text-zinc-500"> ({a.date})</span>}
              </li>
            ))}
          </ul>
        )}
      </Section>
    </div>
  );
}
