/**
 * Typed client for the FastAPI backend.
 *
 * Every call goes through `request`, so auth headers, error shaping, and base-URL
 * handling stay in one place as the API grows.
 */

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type HealthResponse = {
  status: string;
};

export type DbHealthResponse = {
  status: string;
  database?: string;
  detail?: string;
};

export type ServiceInfo = {
  name: string;
  version: string;
  docs: string;
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
      ...init,
    });
  } catch {
    // Network-level failure: backend not running, wrong port, or CORS rejection.
    throw new ApiError(`Could not reach the API at ${API_BASE_URL}${path}`);
  }

  if (!response.ok) {
    throw new ApiError(`${path} responded ${response.status}`, response.status);
  }

  return (await response.json()) as T;
}

export const api = {
  info: () => request<ServiceInfo>("/"),
  health: () => request<HealthResponse>("/health"),
  dbHealth: () => request<DbHealthResponse>("/health/db"),
};
