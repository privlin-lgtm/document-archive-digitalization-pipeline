import type {
  DocumentCreated,
  DocumentDetail,
  DocumentListResponse,
  DocumentStatus,
  EntityCorrectionOut,
  EntityCorrectionRequest,
  FlagSeverity,
  ReviewFlagListResponse,
  ReviewFlagOut,
  ReviewFlagStatus,
  ReviewFlagUpdateRequest,
  SearchFilters,
  SearchResponse,
  StatsResponse,
} from "./types";

// Dev-only auth: a single reviewer role is assumed for MVP (per the stage 8
// spec), so the API token lives in an env var rather than a login flow.
// A future multi-user pass would replace this with real session auth.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const API_TOKEN = import.meta.env.VITE_API_TOKEN ?? "";

export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, body: unknown) {
    super(`API error ${status}: ${JSON.stringify(body)}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

function authHeaders(): HeadersInit {
  return { Authorization: `Bearer ${API_TOKEN}` };
}

async function parseErrorBody(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return await response.text().catch(() => null);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { ...authHeaders(), ...init?.headers },
  });
  if (!response.ok) {
    throw new ApiError(response.status, await parseErrorBody(response));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

function buildQuery<T extends object>(params: T): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  }
  const query = search.toString();
  return query ? `?${query}` : "";
}

export const api = {
  uploadDocuments(files: File[]): Promise<DocumentCreated[]> {
    const formData = new FormData();
    for (const file of files) {
      formData.append("files", file);
    }
    return request<DocumentCreated[]>("/documents", { method: "POST", body: formData });
  },

  listDocuments(params: { status?: DocumentStatus; limit?: number; offset?: number } = {}): Promise<DocumentListResponse> {
    return request<DocumentListResponse>(`/documents${buildQuery(params)}`);
  },

  getDocument(id: string): Promise<DocumentDetail> {
    return request<DocumentDetail>(`/documents/${id}`);
  },

  /**
   * The image endpoint requires a Bearer header, which a plain <img src>
   * can't attach — fetch it as a blob and hand back an object URL instead.
   * Callers must revoke the URL (URL.revokeObjectURL) when done with it.
   */
  async getDocumentImageUrl(id: string, annotate: boolean): Promise<string> {
    const response = await fetch(`${API_BASE_URL}/documents/${id}/image${buildQuery({ annotate })}`, {
      headers: authHeaders(),
    });
    if (!response.ok) {
      throw new ApiError(response.status, await parseErrorBody(response));
    }
    const blob = await response.blob();
    return URL.createObjectURL(blob);
  },

  search(filters: SearchFilters): Promise<SearchResponse> {
    return request<SearchResponse>(`/search${buildQuery(filters)}`);
  },

  correctEntity(entityId: string, body: EntityCorrectionRequest): Promise<EntityCorrectionOut> {
    return request<EntityCorrectionOut>(`/entities/${entityId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  },

  listReviewFlags(
    params: { status?: ReviewFlagStatus | null; severity?: FlagSeverity; limit?: number; offset?: number } = {},
  ): Promise<ReviewFlagListResponse> {
    const { status, ...rest } = params;
    return request<ReviewFlagListResponse>(
      `/review_flags${buildQuery({ ...rest, status: status === null ? "" : status })}`,
    );
  },

  updateReviewFlag(flagId: string, body: ReviewFlagUpdateRequest): Promise<ReviewFlagOut> {
    return request<ReviewFlagOut>(`/review_flags/${flagId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  },

  getStats(): Promise<StatsResponse> {
    return request<StatsResponse>("/stats");
  },
};
