/**
 * The API client. One place that knows where the API is and how a request is
 * authenticated, so nothing else has to.
 *
 * `credentials: "include"` on every call: the session is an httpOnly cookie set
 * by the API, which the app cannot read and therefore cannot leak to a script
 * that gets injected into a page. The cost is that the API and the app are
 * separate origins in development, so the API's CORS policy has to allow
 * credentials from exactly this origin — a wildcard is refused by the browser
 * once credentials are involved, which is the correct outcome.
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    readonly body?: unknown
  ) {
    super(detail || `HTTP ${status}`);
    this.name = "ApiError";
  }

  /** Whether trying the same request again could plausibly work. */
  get retryable(): boolean {
    return this.status === 0 || this.status === 429 || this.status >= 500;
  }
}

export async function api<T>(
  path: string,
  init: RequestInit & { json?: unknown } = {}
): Promise<T> {
  const { json, headers, ...rest } = init;
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...rest,
      credentials: "include",
      headers: {
        ...(json === undefined ? {} : { "Content-Type": "application/json" }),
        ...headers,
      },
      body: json === undefined ? rest.body : JSON.stringify(json),
    });
  } catch (cause) {
    // A network failure is not an HTTP status, and the upload path has to be
    // able to tell "the wifi dropped" from "the server said no".
    throw new ApiError(0, "the network request failed", cause);
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const body = text ? safeJson(text) : undefined;
  if (!response.ok) {
    const detail =
      typeof body === "object" && body !== null && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : response.statusText;
    throw new ApiError(response.status, detail, body);
  }
  return body as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}
