// Minimal typed API client. Kept deliberately small for the MVP shell —
// this is the foundation the dashboard phase will build on, not the
// dashboard itself.
const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";
const HEALTH_URL = API_BASE_URL.replace(/\/api\/v1\/?$/, "") + "/health";

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
  error?: { code?: string; message?: string };
}

async function request<T>(url: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers ?? {}) },
  });

  if (!res.ok) {
    let message = `Request failed with status ${res.status}`;
    let code: string | undefined;
    try {
      const body = (await res.json()) as ErrorEnvelope;
      message = body.error?.message ?? message;
      code = body.error?.code;
    } catch {
      // Response body wasn't JSON (or was empty) — keep the generic message.
    }
    throw new ApiError(message, res.status, code);
  }

  return (await res.json()) as T;
}

export interface HealthStatus {
  status: string;
}

export function getHealth(): Promise<HealthStatus> {
  return request<HealthStatus>(HEALTH_URL);
}
