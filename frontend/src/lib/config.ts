/**
 * Frontend configuration.
 *
 * NEXT_PUBLIC_* variables are inlined into the browser bundle at build time
 * (see Next.js "environment variables" docs), so they must never contain secrets.
 */

export const DEFAULT_API_BASE_URL = "http://localhost:8000";

/**
 * Normalise and validate the backend base URL.
 * Returns `null` when the value is not an absolute http(s) URL, so the UI can
 * report a configuration problem instead of issuing requests to a bad origin.
 */
export function resolveApiBaseUrl(raw: string | undefined): string | null {
  const value = (raw ?? "").trim() || DEFAULT_API_BASE_URL;
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return null;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
  if (parsed.username || parsed.password) return null; // never embed credentials
  return `${parsed.origin}${parsed.pathname}`.replace(/\/+$/, "");
}

export const API_BASE_URL = resolveApiBaseUrl(process.env.NEXT_PUBLIC_API_BASE_URL);
