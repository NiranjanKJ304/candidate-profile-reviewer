/** Visual helpers shared by the developer-platform sections (GitHub, Codeforces). */

import { AVAILABILITY_TEXT, type Availability } from "@/lib/github";

const TONE: Record<Availability, string> = {
  available: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  empty: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  unavailable: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  not_collected: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  permission_denied: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  rate_limited: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  network_error: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  error: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
};

export function Badge({ status }: { status: Availability }) {
  return <span className={`rounded-full px-2 py-0.5 text-xs ${TONE[status]}`}>{AVAILABILITY_TEXT[status]}</span>;
}

export function when(value: string | null): string {
  if (!value) return "never";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
}

export function day(value: string | null): string | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleDateString();
}
