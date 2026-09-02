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
  SessionOut,
  StatsResponse,
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api";

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

async function parseErrorBody(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return await response.text().catch(() => null);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    credentials: "include",
    ...init,
    headers: { ...init?.headers },
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
  login(reviewer: string, password: string): Promise<SessionOut> {
    return request<SessionOut>("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reviewer, password }),
    });
  },

  logout(): Promise<void> {
    return request<void>("/auth/logout", { method: "POST" });
  },

  me(): Promise<SessionOut> {
    return request<SessionOut>("/auth/me");
  },

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

  reprocessDocument(id: string): Promise<DocumentCreated> {
    return request<DocumentCreated>(`/documents/${id}/reprocess`, { method: "POST" });
  },

  async getDocumentImageUrl(id: string, annotate: boolean, page = 1): Promise<string> {
    const response = await fetch(`${API_BASE_URL}/documents/${id}/image${buildQuery({ annotate, page })}`, {
      credentials: "include",
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
