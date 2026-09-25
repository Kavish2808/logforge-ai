// Typed HTTP client for the LogForge API. Every response shape used by the
// UI comes from the backend; nothing here fabricates data.
export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";
export const HEALTH_URL = API_BASE_URL.replace(/\/api\/v1\/?$/, "") + "/health";

export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

interface ErrorEnvelope {
  error?: { code?: string; message?: string; fields?: Record<string, string> };
}

export type Query = Record<string, string | number | boolean | string[] | null | undefined>;

export function buildQuery(params: Query = {}): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) value.forEach((v) => v && qs.append(key, v));
    else qs.append(key, String(value));
  }
  const s = qs.toString();
  return s ? `?${s}` : "";
}

export async function request<T>(
  path: string,
  options: { method?: string; body?: unknown; query?: Query; signal?: AbortSignal; absolute?: boolean } = {},
): Promise<T> {
  const url = (options.absolute ? path : `${API_BASE_URL}${path}`) + buildQuery(options.query);
  let res: Response;
  try {
    res = await fetch(url, {
      method: options.method ?? "GET",
      headers: options.body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal: options.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError("The LogForge API could not be reached.", 0, "NETWORK_ERROR");
  }
  if (!res.ok) {
    let message = `Request failed with status ${res.status}`;
    let code: string | undefined;
    try {
      const body = (await res.json()) as ErrorEnvelope;
      message = body.error?.message ?? message;
      code = body.error?.code;
      if (body.error?.fields) {
        message += ": " + Object.entries(body.error.fields).map(([k, v]) => `${k} ${v}`).join("; ");
      }
    } catch {
      // non-JSON error body: keep the generic message
    }
    throw new ApiError(message, res.status, code);
  }
  return (await res.json()) as T;
}

export function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}
