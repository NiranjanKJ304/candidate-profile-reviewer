"use client";

import { useCallback, useEffect, useState } from "react";

import {
  fetchHealth,
  fetchReadiness,
  type DependencyCheck,
  type FetchResult,
  type HealthInfo,
  type ReadinessInfo,
} from "@/lib/status";

type Tone = "ok" | "warn" | "down" | "unknown" | "loading";

interface Row {
  key: string;
  label: string;
  tone: Tone;
  value: string;
  detail: string | null;
}

interface Snapshot {
  health: FetchResult<HealthInfo>;
  readiness: FetchResult<ReadinessInfo>;
  checkedAt: Date;
}

const TONE_STYLES: Record<Tone, { dot: string; text: string }> = {
  ok: { dot: "bg-emerald-500", text: "text-emerald-700 dark:text-emerald-400" },
  warn: { dot: "bg-amber-500", text: "text-amber-700 dark:text-amber-400" },
  down: { dot: "bg-red-500", text: "text-red-700 dark:text-red-400" },
  unknown: { dot: "bg-zinc-400", text: "text-zinc-600 dark:text-zinc-400" },
  loading: { dot: "bg-zinc-300 animate-pulse", text: "text-zinc-500" },
};

const DEPENDENCIES: { key: string; label: string }[] = [
  { key: "postgres", label: "PostgreSQL" },
  { key: "redis", label: "Redis" },
];

function dependencyRow(key: string, label: string, check: DependencyCheck | undefined): Row {
  if (!check) return { key, label, tone: "unknown", value: "Not reported", detail: null };
  if (check.status === "ok") {
    return {
      key,
      label,
      tone: "ok",
      value: "Connected",
      detail: check.latencyMs !== null ? `${check.latencyMs.toFixed(1)} ms` : null,
    };
  }
  return {
    key,
    label,
    tone: check.status === "timeout" ? "warn" : "down",
    value: check.status === "timeout" ? "Timed out" : "Unavailable",
    detail: check.error,
  };
}

export function buildRows(snapshot: Snapshot | null): Row[] {
  if (!snapshot) {
    return [
      { key: "api", label: "Backend API", tone: "loading", value: "Checking…", detail: null },
      ...DEPENDENCIES.map((d) => ({ ...d, tone: "loading" as Tone, value: "Checking…", detail: null })),
    ];
  }
  const { health, readiness } = snapshot;
  const api: Row =
    health.kind === "ok"
      ? {
          key: "api",
          label: "Backend API",
          tone: "ok",
          value: "Online",
          detail: `v${health.data.version} · ${health.data.environment}`,
        }
      : health.kind === "unreachable"
        ? { key: "api", label: "Backend API", tone: "down", value: "Unreachable", detail: health.message }
        : { key: "api", label: "Backend API", tone: "warn", value: "Invalid response", detail: health.message };

  if (readiness.kind !== "ok") {
    // Without a readiness answer we cannot know dependency state: report "Unknown", not "down".
    const detail = readiness.kind === "unreachable" ? "Readiness not available" : readiness.message;
    return [api, ...DEPENDENCIES.map((d) => ({ ...d, tone: "unknown" as Tone, value: "Unknown", detail }))];
  }
  return [api, ...DEPENDENCIES.map((d) => dependencyRow(d.key, d.label, readiness.data.checks[d.key]))];
}

function overall(rows: Row[]): { tone: Tone; label: string } {
  if (rows.some((r) => r.tone === "loading")) return { tone: "loading", label: "Checking" };
  if (rows.every((r) => r.tone === "ok")) return { tone: "ok", label: "All systems operational" };
  if (rows[0]?.tone !== "ok") return { tone: "down", label: "Backend unavailable" };
  return { tone: "warn", label: "Degraded" };
}

export default function StatusPanel({ apiBaseUrl }: { apiBaseUrl: string | null }) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const check = useCallback(async () => {
    if (!apiBaseUrl) return;
    setRefreshing(true);
    const [health, readiness] = await Promise.all([fetchHealth(apiBaseUrl), fetchReadiness(apiBaseUrl)]);
    setSnapshot({ health, readiness, checkedAt: new Date() });
    setRefreshing(false);
  }, [apiBaseUrl]);

  useEffect(() => {
    // Initial status check on mount; state updates happen after the awaited fetches.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void check();
  }, [check]);

  if (!apiBaseUrl) {
    return (
      <section aria-labelledby="status-heading" className="rounded-xl border border-red-300 p-6 dark:border-red-800">
        <h2 id="status-heading" className="text-lg font-semibold">
          System status
        </h2>
        <p role="alert" className="mt-2 text-sm text-red-700 dark:text-red-400">
          Configuration error: NEXT_PUBLIC_API_BASE_URL is not a valid http(s) URL.
        </p>
      </section>
    );
  }

  const rows = buildRows(snapshot);
  const summary = overall(rows);

  return (
    <section
      aria-labelledby="status-heading"
      className="rounded-xl border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-900"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 id="status-heading" className="text-lg font-semibold">
            System status
          </h2>
          <p data-testid="overall-status" className={`mt-1 text-sm font-medium ${TONE_STYLES[summary.tone].text}`}>
            {summary.label}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void check()}
          disabled={refreshing}
          className="rounded-md border border-zinc-300 px-3 py-1.5 text-sm font-medium hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-800"
        >
          {refreshing ? "Checking…" : "Refresh"}
        </button>
      </div>

      <ul className="mt-5 divide-y divide-zinc-100 dark:divide-zinc-800">
        {rows.map((row) => (
          <li key={row.key} data-testid={`status-${row.key}`} className="flex items-center justify-between gap-4 py-3">
            <span className="flex items-center gap-2.5 text-sm font-medium">
              <span aria-hidden className={`h-2.5 w-2.5 rounded-full ${TONE_STYLES[row.tone].dot}`} />
              {row.label}
            </span>
            <span className="text-right">
              <span className={`block text-sm font-medium ${TONE_STYLES[row.tone].text}`}>{row.value}</span>
              {row.detail && <span className="block text-xs text-zinc-500">{row.detail}</span>}
            </span>
          </li>
        ))}
      </ul>

      <p className="mt-4 text-xs text-zinc-500">
        Backend: <code>{apiBaseUrl}</code>
        {snapshot && <> · checked {snapshot.checkedAt.toLocaleTimeString()}</>}
      </p>
    </section>
  );
}
