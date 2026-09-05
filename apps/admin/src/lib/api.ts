/**
 * The back-office API client.
 *
 * `credentials: "include"` for the same reason the product's client does it:
 * the session is an httpOnly cookie the browser holds and this app cannot read,
 * so it cannot leak it to injected script. The admin API's CORS policy names
 * this origin and only this one.
 *
 * The default points at loopback because that is where the admin API is
 * supposed to be — it refuses to bind anywhere else without an explicit
 * override — so a back-office UI that "works" against a public address is a
 * signal that something upstream is wrong.
 */

export const ADMIN_API =
  process.env.NEXT_PUBLIC_ADMIN_API_URL?.replace(/\/$/, "") ??
  "http://localhost:8001";

export class AdminError extends Error {
  constructor(readonly status: number, readonly detail: string) {
    super(detail || `HTTP ${status}`);
    this.name = "AdminError";
  }
}

async function request<T>(
  path: string,
  init: RequestInit & { json?: unknown } = {}
): Promise<T> {
  const { json, headers, ...rest } = init;
  let response: Response;
  try {
    response = await fetch(`${ADMIN_API}/admin/v1${path}`, {
      ...rest,
      credentials: "include",
      headers: {
        ...(json === undefined ? {} : { "Content-Type": "application/json" }),
        ...headers,
      },
      body: json === undefined ? rest.body : JSON.stringify(json),
    });
  } catch {
    throw new AdminError(
      0,
      `cannot reach the back-office API at ${ADMIN_API}. Start it with: ` +
        `uvicorn mishne.admin.main:app --host 127.0.0.1 --port 8001`
    );
  }

  if (response.status === 204) return undefined as T;
  const text = await response.text();
  const body = text ? safeJson(text) : undefined;
  if (!response.ok) {
    throw new AdminError(response.status, detailOf(body) || response.statusText);
  }
  return body as T;
}

/**
 * FastAPI's 422 puts a list of field errors in `detail`, and rendering
 * `[object Object]` at an operator about to move money is not acceptable.
 */
function detailOf(body: unknown): string {
  if (typeof body !== "object" || body === null) return "";
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((e) => {
        const field = Array.isArray(e?.loc) ? e.loc.slice(1).join(".") : "";
        return field ? `${field}: ${e.msg}` : String(e?.msg ?? "");
      })
      .join("; ");
  }
  return "";
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export const get = <T,>(path: string) => request<T>(path);
export const post = <T,>(path: string, json?: unknown) =>
  request<T>(path, { method: "POST", json: json ?? {} });
export const patch = <T,>(path: string, json: unknown) =>
  request<T>(path, { method: "PATCH", json });

// ── what the API returns ──────────────────────────────────────────────────

export type Org = {
  id: string;
  name: string;
  tier: "starter" | "pro" | "studio";
  retention_days: number;
  created_at: string;
  suspended_at: string | null;
  suspended_reason: string | null;
  available: number;
  held: number;
  user_count: number;
  project_count: number;
  job_count: number;
};

export type LedgerRow = {
  id: string;
  kind: string;
  delta: number;
  balance_after: number;
  description: string;
  project_id: string | null;
  job_id: string | null;
  created_at: string;
};

export type OrgDetail = Org & {
  members: Array<{
    id: string;
    email: string;
    name: string;
    role: string;
    created_at: string;
  }>;
  projects: Array<{
    id: string;
    name: string;
    created_at: string;
    job_count: number;
    archived: boolean;
  }>;
  recent_jobs: Array<{
    id: string;
    project_id: string;
    status: string;
    mode: string;
    created_at: string;
    approved_cap: number;
    credits_settled: number;
  }>;
  ledger: LedgerRow[];
};

export type Action = {
  id: string;
  admin_id: string | null;
  admin_email: string | null;
  action: string;
  target_org_id: string | null;
  target_id: string | null;
  reason: string;
  detail: Record<string, unknown>;
  created_at: string;
};

export type Overview = {
  orgs: number;
  suspended: number;
  credits_outstanding: number;
  credits_held: number;
  jobs_running: number;
  jobs_failed_24h: number;
};

/**
 * A job as the back-office sees it.
 *
 * No `name`: a job's name defaults to the first upload it draws on, so it is a
 * filename by another route, and filenames are customer content
 * (`admin/service.py`). The org, the project and the timestamp identify a job
 * for support without describing what the customer shot.
 */
export type AdminJob = {
  id: string;
  org_id: string;
  org_name: string | null;
  project_id: string | null;
  project_name: string | null;
  mode: string;
  status: string;
  error: Record<string, unknown> | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  approved_cap: number;
  credits_settled: number;
  cost_cents: number;
  media_gaps: Record<string, unknown> | null;
  seconds: number | null;
};

export type JobStep = {
  idx: number;
  name: string;
  status: "pending" | "active" | "done" | "failed";
  attempt: number;
  detail: string | null;
  error: Record<string, unknown> | null;
  asset_id: string | null;
  seconds: number;
  from_cache: boolean;
  model_cost_micros: number;
  started_at: string | null;
  finished_at: string | null;
};

export type AdminJobDetail = AdminJob & {
  model_versions: Record<string, unknown> | null;
  failed_step: string | null;
  steps: JobStep[];
  artifacts: Array<{
    id: string;
    kind: string;
    bytes: number;
    validated: boolean;
  }>;
  assets: Array<{
    id: string;
    kind: string;
    status: string;
    bytes: number;
    duration_frames: number | null;
    proxy_status: string;
    error: string | null;
  }>;
};
