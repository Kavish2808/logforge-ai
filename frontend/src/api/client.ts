// Typed HTTP client for the LogForge API. Every response shape used by the
// UI comes from the backend; nothing here fabricates data.
import { clearAuth, getToken } from "../lib/auth";

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

type Options = { method?: string; body?: unknown; query?: Query; signal?: AbortSignal; absolute?: boolean };

function headersFor(options: Options): Record<string, string> | undefined {
  const headers: Record<string, string> = {};
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;  // Phase 7 RBAC (only when signed in)
  return Object.keys(headers).length ? headers : undefined;
}

async function send(path: string, options: Options): Promise<Response> {
  const url = (options.absolute ? path : `${API_BASE_URL}${path}`) + buildQuery(options.query);
  let res: Response;
  try {
    res = await fetch(url, {
      method: options.method ?? "GET",
      headers: headersFor(options),
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal: options.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError("The LogForge API could not be reached.", 0, "NETWORK_ERROR");
  }
  if (!res.ok) throw await toError(res);
  return res;
}

async function toError(res: Response): Promise<ApiError> {
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
  if (res.status === 401 && getToken()) clearAuth();  // expired / revoked session
  return new ApiError(message, res.status, code);
}

export async function request<T>(path: string, options: Options = {}): Promise<T> {
  return (await (await send(path, options)).json()) as T;
}

/** Streams a file response (export) into a Blob; returns it with the response headers. */
export async function download(path: string, options: Options = {}): Promise<{ blob: Blob; headers: Headers }> {
  const res = await send(path, options);
  return { blob: await res.blob(), headers: res.headers };
}

export function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}
