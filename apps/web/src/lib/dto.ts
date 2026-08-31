/**
 * The wire, and the app's own shape.
 *
 * The API speaks snake_case (`credits_used`, `start_tc_frames`) and
 * `@mishne/shared` — which is the contract the screens are written against —
 * speaks camelCase (`creditsUsed`, `startTcFrames`). Every field therefore
 * exists in two spellings, and something has to convert.
 *
 * ## Why one recursive function rather than a mapper per type
 *
 * Hand-written mappers would be a third copy of every field name, after
 * `schemas.py` and `types.ts`. Three copies drift, and the way this one would
 * drift is the worst kind: a field added to both ends and forgotten in the
 * middle reads as `undefined` in a component that is typed as though it cannot
 * be. The conversion is mechanical — `a_b` becomes `aB`, always — so it is
 * done mechanically, once.
 *
 * The rule holds for every response the screens read: no key in `schemas.py`
 * carries meaning in its underscores, and no map in the API is keyed by
 * customer data. `upload.ts` and `session-provider.tsx` deliberately do NOT go
 * through this — they declare their own snake_case wire types and were written
 * before this existed. Leaving them is not an oversight: they are the two paths
 * where the raw response shape is load-bearing, and converting them would be a
 * change with no reader asking for it.
 *
 * ## Dates
 *
 * `created_at` arrives as an ISO string and `types.ts` types it as a string.
 * It stays a string — parsing it here would make every consumer that formats
 * it re-serialise, and the API's format is already the one `Date` accepts.
 */

import { api } from "./api";

/** `snake_case` to `camelCase`, recursively, structure otherwise untouched. */
export function camelize<T>(value: unknown): T {
  if (Array.isArray(value)) return value.map((v) => camelize(v)) as T;
  if (value === null || typeof value !== "object") return value as T;
  // Date and friends have no enumerable own keys and would come back empty.
  if (Object.getPrototypeOf(value) !== Object.prototype) return value as T;

  const out: Record<string, unknown> = {};
  for (const [key, inner] of Object.entries(value as Record<string, unknown>)) {
    out[key.replace(/_([a-z0-9])/g, (_, c: string) => c.toUpperCase())] =
      camelize(inner);
  }
  return out as T;
}

/** A GET whose body comes back in the app's own casing. */
export async function apiGet<T>(path: string): Promise<T> {
  return camelize<T>(await api<unknown>(path));
}

/** A POST/PATCH whose response comes back in the app's own casing. */
export async function apiSend<T>(
  path: string,
  init: Parameters<typeof api>[1] = {}
): Promise<T> {
  return camelize<T>(await api<unknown>(path, { method: "POST", ...init }));
}
