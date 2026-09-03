import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./client";

function mockFetchOnce(response: Partial<Response> & { json?: () => Promise<unknown> }) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({}),
    ...response,
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("api client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends credentials so the HttpOnly session cookie is included", async () => {
    const fetchMock = mockFetchOnce({ json: async () => ({ reviewer: "paul" }) });

    await api.me();

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/auth/me"),
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("raises ApiError with the parsed body on a non-2xx JSON response", async () => {
    mockFetchOnce({ ok: false, status: 401, json: async () => ({ detail: "invalid credentials" }) });

    await expect(api.login("paul", "wrong")).rejects.toMatchObject({
      status: 401,
      body: { detail: "invalid credentials" },
    });
  });

  it("falls back to text when the error body isn't JSON", async () => {
    mockFetchOnce({
      ok: false,
      status: 500,
      json: async () => {
        throw new Error("not json");
      },
      text: async () => "internal server error",
    });

    let caught: ApiError | undefined;
    try {
      await api.getStats();
    } catch (err) {
      caught = err as ApiError;
    }

    expect(caught).toBeInstanceOf(ApiError);
    expect(caught?.status).toBe(500);
    expect(caught?.body).toBe("internal server error");
  });

  it("returns undefined for a 204 No Content response instead of parsing a body", async () => {
    mockFetchOnce({
      ok: true,
      status: 204,
      json: async () => {
        throw new Error("should not be called for 204");
      },
    });

    await expect(api.logout()).resolves.toBeUndefined();
  });

  it("omits empty query params instead of sending them as empty strings", async () => {
    const fetchMock = mockFetchOnce({ json: async () => ({ results: [], limit: 50, offset: 0, total: 0 }) });

    await api.listDocuments({ status: undefined, limit: 50, offset: 0 });

    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain("limit=50");
    expect(url).not.toContain("status=");
  });
});
