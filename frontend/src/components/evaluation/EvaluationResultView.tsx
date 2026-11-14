import {
  type AssessedRequirement,
  type EvaluationResult,
  type EvidenceItem,
  FIT_TEXT,
  type FitLabel,
  TRUST_TEXT,
  type Trust,
  VERDICT_TEXT,
  type Verdict,
} from "@/lib/evaluations";

const VERDICT_TONE: Record<Verdict, string> = {
  met: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  partially_met: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  not_met: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  insufficient_evidence: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
};
const FIT_TONE: Record<FitLabel, string> = {
  strong: "text-emerald-700 dark:text-emerald-400",
  moderate: "text-amber-700 dark:text-amber-400",
  weak: "text-red-700 dark:text-red-400",
  insufficient_evidence: "text-zinc-600 dark:text-zinc-400",
};
const TRUST_TONE: Record<Trust, string> = {
  platform_collected: "border-emerald-300 text-emerald-800 dark:border-emerald-800 dark:text-emerald-300",
  resume_claim: "border-sky-300 text-sky-800 dark:border-sky-800 dark:text-sky-300",
  self_reported: "border-amber-300 text-amber-800 dark:border-amber-800 dark:text-amber-300",
};

function EvidenceChips({ ids, evidence }: { ids: string[]; evidence: Map<string, EvidenceItem> }) {
  if (ids.length === 0) return null;
  return (
    <span className="flex flex-wrap gap-1">
      {ids.map((id) => {
        const item = evidence.get(id);
        return (
          <a
            key={id}
            href={`#evidence-${id}`}
            title={item ? `${TRUST_TEXT[item.trust]}: ${item.text}` : id}
            className={`rounded border px-1.5 text-xs ${item ? TRUST_TONE[item.trust] : "border-zinc-300"}`}
          >
            {id}
          </a>
        );
      })}
    </span>
  );
}

function RequirementRow({ r, evidence }: { r: AssessedRequirement; evidence: Map<string, EvidenceItem> }) {
  return (
    <tr className="border-t border-zinc-100 align-top dark:border-zinc-800">
      <td className="py-2 pr-3">
        <p className="font-medium">{r.text}</p>
        <p className="text-xs text-zinc-500">
          {r.kind} · {r.type}
        </p>
      </td>
      <td className="py-2 pr-3">
        <span className={`whitespace-nowrap rounded-full px-2 py-0.5 text-xs ${VERDICT_TONE[r.verdict]}`}>{VERDICT_TEXT[r.verdict]}</span>
      </td>
      <td className="py-2">
        <p>{r.rationale}</p>
        {r.adjusted && <p className="text-xs italic text-zinc-500">Adjusted by the evaluation rules: {r.adjusted}</p>}
        <EvidenceChips ids={r.evidence_ids} evidence={evidence} />
      </td>
    </tr>
  );
}

export default function EvaluationResultView({ result }: { result: EvaluationResult }) {
  const evidence = new Map(result.evidence.map((e) => [e.id, e]));
  const byId = new Map(result.requirements.map((r) => [r.id, r]));
  const s = result.score;
  const names = (reqIds: string[]) => reqIds.map((id) => byId.get(id)?.text ?? id).join(", ");

  return (
    <div className="grid gap-5" data-testid="evaluation-result">
      <p role="note" className="rounded-md border border-zinc-300 bg-zinc-50 p-3 text-xs text-zinc-700 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-300">
        {result.decision_support_notice}
      </p>

      <div className="grid gap-4 sm:grid-cols-[12rem_1fr]">
        <div className="rounded-lg border border-zinc-200 p-4 text-center dark:border-zinc-800">
          <p className="text-4xl font-bold" data-testid="evaluation-score">
            {s.overall !== null ? Math.round(s.overall) : "—"}
          </p>
          <p className="text-xs text-zinc-500">{s.overall !== null ? "out of 100" : "no score"}</p>
          <p className={`mt-1 text-sm font-semibold ${FIT_TONE[s.fit_label]}`}>{FIT_TEXT[s.fit_label]}</p>
        </div>
        <dl className="grid gap-2 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-xs uppercase tracking-wide text-zinc-500">Required met</dt>
            <dd>
              {s.required_met} / {s.required_total}
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-zinc-500">Preferred met</dt>
            <dd>
              {s.preferred_met} / {s.preferred_total}
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-zinc-500">Evidence coverage</dt>
            <dd>{Math.round(s.coverage * 100)}% of requirement weight could be judged</dd>
          </div>
          {s.unmet_required.length > 0 && (
            <div className="sm:col-span-3">
              <dt className="text-xs uppercase tracking-wide text-red-700 dark:text-red-400">Required, not met</dt>
              <dd>{names(s.unmet_required)}</dd>
            </div>
          )}
          {s.unevidenced_required.length > 0 && (
            <div className="sm:col-span-3">
              <dt className="text-xs uppercase tracking-wide text-amber-700 dark:text-amber-400">Required, no evidence (unproven, not failed)</dt>
              <dd data-testid="unevidenced">{names(s.unevidenced_required)}</dd>
            </div>
          )}
          <div className="sm:col-span-3 text-xs text-zinc-500">
            Experience estimate: {result.experience.years !== null ? `${result.experience.years} years` : "unknown"} · JD version:{" "}
            {result.jd_version} · {result.privacy.identity_removed ? "identity removed before the LLM call" : ""}
          </div>
        </dl>
      </div>

      <div>
        <h3 className="text-sm font-semibold">Model summary</h3>
        <p className="text-sm text-zinc-700 dark:text-zinc-300">{result.summary}</p>
        <p className="text-xs text-zinc-500">Written by the LLM and may contain mistakes; the requirement table below is authoritative.</p>
      </div>

      <div className="overflow-x-auto">
        <h3 className="text-sm font-semibold">Requirements</h3>
        <table className="mt-2 w-full text-left text-sm" aria-label="Requirement verdicts">
          <thead className="text-xs text-zinc-500">
            <tr>
              <th className="py-1 font-normal">Requirement</th>
              <th className="py-1 font-normal">Verdict</th>
              <th className="py-1 font-normal">Why (evidence)</th>
            </tr>
          </thead>
          <tbody>
            {result.requirements.map((r) => (
              <RequirementRow key={r.id} r={r} evidence={evidence} />
            ))}
          </tbody>
        </table>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        {(
          [
            ["Strengths", result.strengths],
            ["Discrepancies", result.discrepancies],
          ] as const
        ).map(([title, items]) => (
          <div key={title}>
            <h3 className="text-sm font-semibold">{title}</h3>
            {items.length === 0 ? (
              <p className="text-xs italic text-zinc-500">None noted.</p>
            ) : (
              <ul className="mt-1 grid gap-1 text-sm">
                {items.map((p) => (
                  <li key={p.text}>
                    {p.text} <EvidenceChips ids={p.evidence_ids} evidence={evidence} />
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
        <div>
          <h3 className="text-sm font-semibold">Gaps</h3>
          {result.gaps.length === 0 ? (
            <p className="text-xs italic text-zinc-500">None noted.</p>
          ) : (
            <ul className="mt-1 grid gap-1 text-sm">
              {result.gaps.map((g) => (
                <li key={g.text}>{g.text}</li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div>
        <h3 className="text-sm font-semibold">Sources</h3>
        <ul className="mt-1 flex flex-wrap gap-2 text-xs">
          {result.sources.map((src) => (
            <li key={src.source} className="rounded border border-zinc-200 px-2 py-1 dark:border-zinc-800">
              {src.source}: {src.status.replace("_", " ")}
              {src.note ? ` (${src.note})` : ""}
            </li>
          ))}
        </ul>
        <p className="mt-1 text-xs text-zinc-500">An unlinked or unavailable source is not evidence of missing skills.</p>
      </div>

      <details>
        <summary className="cursor-pointer text-sm font-semibold">Evidence used ({result.evidence.length} items)</summary>
        <ul className="mt-2 grid gap-1 text-xs">
          {result.evidence.map((e) => (
            <li key={e.id} id={`evidence-${e.id}`} className="flex flex-wrap gap-2">
              <span className="font-mono">{e.id}</span>
              <span className={`rounded border px-1 ${TRUST_TONE[e.trust]}`}>{TRUST_TEXT[e.trust]}</span>
              <span>{e.text}</span>
              {e.source_url && (
                <a href={e.source_url} target="_blank" rel="noopener noreferrer" className="underline">
                  source
                </a>
              )}
            </li>
          ))}
        </ul>
      </details>

      <details>
        <summary className="cursor-pointer text-xs text-zinc-500">How the score is calculated</summary>
        <p className="mt-1 text-xs text-zinc-500">{s.method}</p>
      </details>
    </div>
  );
}
