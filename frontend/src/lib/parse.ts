/** Small runtime-validation helpers for untrusted API payloads. */

export class Invalid extends Error {}

export function rec(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Invalid();
  return value as Record<string, unknown>;
}
export function str(value: unknown): string {
  if (typeof value !== "string") throw new Invalid();
  return value;
}
export function optStr(value: unknown): string | null {
  return value === null || value === undefined ? null : str(value);
}
export function num(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new Invalid();
  return value;
}
export function optNum(value: unknown): number | null {
  return value === null || value === undefined ? null : num(value);
}
export function bool(value: unknown): boolean {
  if (typeof value !== "boolean") throw new Invalid();
  return value;
}
export function optBool(value: unknown): boolean | null {
  return value === null || value === undefined ? null : bool(value);
}
export function list<T>(value: unknown, item: (v: unknown) => T): T[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) throw new Invalid();
  return value.map(item);
}
export function oneOf<T extends string>(value: unknown, allowed: readonly T[]): T {
  const s = str(value);
  if (!(allowed as readonly string[]).includes(s)) throw new Invalid();
  return s as T;
}
export function optOneOf<T extends string>(value: unknown, allowed: readonly T[]): T | null {
  return value === null || value === undefined ? null : oneOf(value, allowed);
}
/** Run a parser; return null on validation failure (other errors propagate). */
export function safe<T>(fn: () => T): T | null {
  try {
    return fn();
  } catch (err) {
    if (err instanceof Invalid) return null;
    throw err;
  }
}
