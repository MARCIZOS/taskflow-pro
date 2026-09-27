import type {
  AIMetrics,
  Board,
  MutationResult,
  Status,
  Suggestion,
  SuggestRun,
  ValidationResult,
} from "./types";

/** Error returned by the API: `{ error: { code, message, ...extra } }`. */
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

const BASE = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "") ?? "";

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/api${path}`, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network", "Can't reach the server. Check your connection and try again.");
  }
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const err = data?.error ?? {};
    const { code = "http_error", message = `Request failed (${res.status})`, ...rest } = err;
    throw new ApiError(res.status, code, message, rest);
  }
  return data as T;
}

export const api = {
  board: () => request<Board>("GET", "/board"),

  createTask: (t: { title: string; description: string; start_date: string; end_date: string }) =>
    request<MutationResult>("POST", "/tasks", t),

  updateTask: (
    id: number,
    patch: { version: number; title?: string; description?: string; start_date?: string; end_date?: string },
  ) => request<MutationResult>("PATCH", `/tasks/${id}`, patch),

  moveTask: (id: number, version: number, status: Status, index: number) =>
    request<MutationResult>("POST", `/tasks/${id}/move`, { version, status, index }),

  deleteTask: (id: number) => request<MutationResult>("DELETE", `/tasks/${id}`),

  addDependency: (predecessor_id: number, successor_id: number) =>
    request<MutationResult>("POST", "/dependencies", { predecessor_id, successor_id }),

  validateDependency: (predecessor_id: number, successor_id: number) =>
    request<ValidationResult>("POST", "/dependencies/validate", { predecessor_id, successor_id }),

  removeDependency: (id: number) => request<MutationResult>("DELETE", `/dependencies/${id}`),

  suggest: () => request<SuggestRun>("POST", "/ai/suggestions"),
  pendingSuggestions: () => request<Suggestion[]>("GET", "/ai/suggestions?status=pending"),
  acceptSuggestion: (id: number) => request<MutationResult>("POST", `/ai/suggestions/${id}/accept`),
  rejectSuggestion: (id: number) => request<void>("POST", `/ai/suggestions/${id}/reject`),
  aiMetrics: () => request<AIMetrics>("GET", "/ai/metrics"),

  resetDemo: () => request<Board>("POST", "/demo/reset"),
};
