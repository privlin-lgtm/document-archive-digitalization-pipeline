import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { useDocumentImage } from "./useDocumentImage";

describe("useDocumentImage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads the image URL and clears loading/error state", async () => {
    vi.spyOn(api, "getDocumentImageUrl").mockResolvedValue("blob:fake-url");

    const { result } = renderHook(() => useDocumentImage("doc-1", false));

    expect(result.current.isLoading).toBe(true);
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(result.current.imageUrl).toBe("blob:fake-url");
    expect(result.current.error).toBeNull();
  });

  it("does nothing when documentId is undefined", () => {
    const { result } = renderHook(() => useDocumentImage(undefined, false));

    expect(result.current.isLoading).toBe(false);
    expect(result.current.imageUrl).toBeNull();
  });

  it("surfaces a fetch failure as error state, not a thrown exception", async () => {
    vi.spyOn(api, "getDocumentImageUrl").mockRejectedValue(new Error("network down"));

    const { result } = renderHook(() => useDocumentImage("doc-1", false));

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.error?.message).toBe("network down");
    expect(result.current.imageUrl).toBeNull();
  });

  it("revokes the previous object URL when documentId changes", async () => {
    const revokeSpy = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    vi.spyOn(api, "getDocumentImageUrl")
      .mockResolvedValueOnce("blob:doc-1")
      .mockResolvedValueOnce("blob:doc-2");

    const { result, rerender } = renderHook(({ id }) => useDocumentImage(id, false), {
      initialProps: { id: "doc-1" },
    });
    await waitFor(() => expect(result.current.imageUrl).toBe("blob:doc-1"));

    rerender({ id: "doc-2" });
    await waitFor(() => expect(result.current.imageUrl).toBe("blob:doc-2"));

    expect(revokeSpy).toHaveBeenCalledWith("blob:doc-1");
  });
});
