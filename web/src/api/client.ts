/**
 * The one place the frontend talks to `/web/*`.
 *
 * Four conventions the backend established (WEB-1, §8.9) and this file is where
 * the UI keeps its side of them:
 *
 * **1 · Errors are RFC 9457 `problem+json`.** Every failure — a use-case refusal,
 * a Pydantic 422, a 404 from an unmatched route, a 500 — comes back in the same
 * shape. So there is one error type here, `ProblemError`, and a component never
 * has to guess whether it got `{detail}` or `{errors}`. Its `type` is a stable
 * URI: match on that, never on the human-readable title.
 *
 * **2 · Cookies, not tokens.** `credentials: "same-origin"` and no
 * `Authorization` header anywhere in this app: the session lives in an HttpOnly
 * cookie precisely so that a script — including ours — cannot read it. In
 * development the Vite proxy keeps the API same-origin so the cookie travels.
 *
 * **3 · `If-Match` is mandatory on a mutation, not optional.** `write()` takes the
 * `rev` the user was looking at. A 409 is not an error to swallow: it means
 * somebody else saved first, and the caller has to re-read. Skipping the header
 * would make the loser of a race silently overwrite the winner, with no error
 * anywhere.
 *
 * **4 · The cursor is opaque.** It is passed back verbatim and never parsed —
 * reading it would make the server's sort order part of this app's assumptions,
 * and then changing that order breaks us.
 */
import type { components } from "./schema";

/**
 * The generated problem shape, widened to a record.
 *
 * Widened because the *handlers* add fields the schema cannot describe — `rev` on
 * a 409, `limit` on a 429 — so a strict type would make reading them a cast at
 * every call site. `ProblemError` pulls the documented fields out by name and
 * keeps the rest in `extra`.
 */
type Problem = components["schemas"]["Problem"] & Record<string, unknown>;

/** A field-level validation message, as the 422 handler emits them. */
export interface FieldError {
  field: string;
  message: string;
  type: string;
}

/**
 * A failed request, carrying the problem document.
 *
 * Thrown rather than returned so that a caller cannot forget to check: an
 * un-awaited failure becomes an unhandled rejection the store's error state
 * catches, instead of an `undefined` that renders as a blank page.
 */
export class ProblemError extends Error {
  readonly status: number;
  /** Stable URI. The thing to branch on. */
  readonly type: string;
  readonly detail?: string;
  readonly errors: FieldError[];
  /** Anything the use case added — e.g. `rev` on a 409. */
  readonly extra: Record<string, unknown>;

  constructor(status: number, body: Problem) {
    const record = body as Record<string, unknown>;
    super((record.title as string) ?? `请求失败（HTTP ${status}）`);
    this.name = "ProblemError";
    this.status = status;
    this.type = (record.type as string) ?? "about:blank";
    this.detail = record.detail as string | undefined;
    this.errors = (record.errors as FieldError[] | undefined) ?? [];
    const { type: _t, title: _ti, status: _s, detail: _d, errors: _e, ...rest } = record;
    this.extra = rest;
  }

  /** The session is gone. The router sends these to the login screen. */
  get isUnauthenticated(): boolean {
    return this.status === 401;
  }

  /** Somebody saved first. `currentRev` says what to re-read against. */
  get isConflict(): boolean {
    return this.status === 409;
  }

  get currentRev(): number | undefined {
    const rev = this.extra.rev;
    return typeof rev === "number" ? rev : undefined;
  }

  /**
   * A second factor is outstanding — a *step in a flow*, not a failed request.
   * The backend answers 401 with this code rather than 200 so a client cannot
   * mistake a half-open session for a usable one.
   */
  get needsMfa(): boolean {
    return this.type.endsWith("mfa_required");
  }
}

const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

interface RequestOptions {
  method?: string;
  body?: unknown;
  /** The revision the user was looking at. Required by every mutation route. */
  rev?: number;
  query?: Record<string, string | number | boolean | string[] | undefined | null>;
  signal?: AbortSignal;
}

function url(path: string, query?: RequestOptions["query"]): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    // Repeated keys rather than a comma-joined string: the backend declares
    // `status` and `priority` as lists, and a comma would arrive as one value.
    if (Array.isArray(value)) value.forEach((one) => params.append(key, String(one)));
    else params.append(key, String(value));
  }
  const rendered = params.toString();
  return rendered ? `${path}?${rendered}` : path;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = {};
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (options.rev !== undefined) headers["If-Match"] = String(options.rev);

  const response = await fetch(url(path, options.query), {
    method,
    headers,
    // Same-origin only. The API is proxied in development for exactly this
    // reason — see vite.config.ts.
    credentials: "same-origin",
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal,
  });

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const parsed: unknown = text ? JSON.parse(text) : null;

  if (!response.ok) {
    throw new ProblemError(response.status, (parsed ?? {}) as Problem);
  }
  return parsed as T;
}

/**
 * Upload goes through `FormData`, so it does not use `request()`.
 *
 * Deliberately not sharing the JSON path: setting `Content-Type` by hand on a
 * `FormData` body drops the multipart boundary, and the failure is a 422 that
 * looks like the file was rejected. Letting the browser set it is the fix.
 */
async function upload(
  path: string,
  file: File,
  fields: Record<string, string>,
): Promise<unknown> {
  const form = new FormData();
  for (const [key, value] of Object.entries(fields)) form.append(key, value);
  form.append("file", file);

  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    body: form,
  });
  const text = await response.text();
  const parsed: unknown = text ? JSON.parse(text) : null;
  if (!response.ok) throw new ProblemError(response.status, (parsed ?? {}) as Problem);
  return parsed;
}

export const api = {
  get: <T>(path: string, query?: RequestOptions["query"], signal?: AbortSignal) =>
    request<T>(path, { query, signal }),
  post: <T>(path: string, body?: unknown, rev?: number) =>
    request<T>(path, { method: "POST", body, rev }),
  // ``rev`` is optional at this layer and required by the *routes that have one*:
  // a ticket PATCH without it is a 422 from the server, which is the right place
  // for that rule to live. A log PATCH has no rev — see stores/logs.ts.
  patch: <T>(path: string, body: unknown, rev?: number) =>
    request<T>(path, { method: "PATCH", body, rev }),
  put: <T>(path: string, body: unknown, rev?: number) =>
    request<T>(path, { method: "PUT", body, rev }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  upload,
  isUnsafe: (method: string) => UNSAFE.has(method.toUpperCase()),
};
