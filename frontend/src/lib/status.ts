/**
 * Typed client for the backend liveness (/health) and readiness (/ready) endpoints.
 *
 * Responses are treated as untrusted input and validated before use. Network
 * failures ("unreachable") are kept distinct from malformed responses ("invalid")
 * so the UI never presents an unknown state as a known failure. Transport lives in
 * apiClient.ts; this module only defines the payload types and parsers.
 */

import { createApiClient, type FetchResult } from "@/lib/apiClient";

export { DEFAULT_TIMEOUT_MS, getJson as fetchEndpoint, type FetchResult } from "@/lib/apiClient";

export type DependencyStatus = "ok" | "error" | "timeout";

export interface DependencyCheck {
  status: DependencyStatus;
  latencyMs: number | null;
  error: string | null;
}

export interface HealthInfo {
  status: "ok";
  service: string;
  version: string;
  environment: string;
  timestamp: string;
}

export interface ReadinessInfo {
  status: "ready" | "not_ready";
  checks: Record<string, DependencyCheck>;
  timestamp: string;
}

const DEPENDENCY_STATUSES: readonly string[] = ["ok", "error", "timeout"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

export function parseHealth(json: unknown): HealthInfo | null {
  if (!isRecord(json)) return null;
  const { status, service, version, environment, timestamp } = json;
  if (status !== "ok") return null;
  if (![service, version, environment, timestamp].every(isString)) return null;
  return {
    status,
    service: service as string,
    version: version as string,
    environment: environment as string,
    timestamp: timestamp as string,
  };
}

function parseCheck(json: unknown): DependencyCheck | null {
  if (!isRecord(json)) return null;
  const { status, latency_ms: latency, error } = json;
  if (!isString(status) || !DEPENDENCY_STATUSES.includes(status)) return null;
  if (latency !== null && latency !== undefined && typeof latency !== "number") return null;
  if (error !== null && error !== undefined && !isString(error)) return null;
  return {
    status: status as DependencyStatus,
    latencyMs: typeof latency === "number" ? latency : null,
    error: isString(error) ? error : null,
  };
}

export function parseReadiness(json: unknown): ReadinessInfo | null {
  if (!isRecord(json)) return null;
  const { status, checks, timestamp } = json;
  if (status !== "ready" && status !== "not_ready") return null;
  if (!isString(timestamp) || !isRecord(checks)) return null;
  const parsed: Record<string, DependencyCheck> = {};
  for (const [name, raw] of Object.entries(checks)) {
    const check = parseCheck(raw);
    if (!check) return null;
    parsed[name] = check;
  }
  return { status, checks: parsed, timestamp };
}

export function fetchHealth(baseUrl: string, timeoutMs?: number): Promise<FetchResult<HealthInfo>> {
  return createApiClient(baseUrl).get("/health", parseHealth, { timeoutMs });
}

export function fetchReadiness(baseUrl: string, timeoutMs?: number): Promise<FetchResult<ReadinessInfo>> {
  return createApiClient(baseUrl).get("/ready", parseReadiness, { timeoutMs, acceptStatuses: [503] });
}
