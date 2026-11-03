import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { DocumentDetail, PageOut } from "../api/types";
import { DocumentViewPage } from "./DocumentViewPage";

function page(n: number): PageOut {
  return {
    id: `page-${n}`,
    page_number: n,
    regions: [
      {
        id: `region-${n}`,
        bbox_x: 10,
        bbox_y: 10,
        bbox_w: 100,
        bbox_h: 40,
        region_type: "paragraph",
        reading_order: 0,
        confidence: 0.9,
        ocr_result: { engine: "tesseract", text: `text on page ${n}`, confidence: 0.9, status: "ok" },
        entities: [],
      },
    ],
  };
}

function threePageDocument(flagOnPage: number | null): DocumentDetail {
  return {
    id: "doc-1",
    filename: "deed.pdf",
    upload_time: "2026-11-02T10:00:00Z",
    status: flagOnPage ? "needs_review" : "ready",
    pages: [page(1), page(2), page(3)],
    flags: flagOnPage
      ? [
          {
            id: "flag-1",
            flag_type: "illegible",
            severity: "high",
            explanation: "Faded ink",
            status: "open",
            page_id: `page-${flagOnPage}`,
            region_id: `region-${flagOnPage}`,
            entity_id: null,
            created_at: "2026-11-02T10:00:00Z",
            status_changed_at: null,
          },
        ]
      : [],
  };
}

function renderView(document: DocumentDetail) {
  vi.spyOn(api, "getDocument").mockResolvedValue(document);
  const imageSpy = vi.spyOn(api, "getDocumentImageUrl").mockResolvedValue("blob:page");
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MantineProvider>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/documents/doc-1"]}>
          <Routes>
            <Route path="/documents/:documentId" element={<DocumentViewPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  );
  return imageSpy;
}

function lastRequestedPage(spy: ReturnType<typeof renderView>): number | undefined {
  const calls = spy.mock.calls;
  return calls.length ? (calls[calls.length - 1][2] as number) : undefined;
}

describe("DocumentViewPage with a multi-page document", () => {
  beforeAll(() => {
    // jsdom has no scrollIntoView; EntityPanel calls it on selection.
    if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("starts on page 1 and switches pages with the page switcher", async () => {
    const imageSpy = renderView(threePageDocument(null));

    await screen.findByText(/Page 1 of 3/);
    await waitFor(() => expect(lastRequestedPage(imageSpy)).toBe(1));

    fireEvent.click(screen.getByRole("button", { name: "Next page" }));

    await screen.findByText(/Page 2 of 3/);
    await waitFor(() => expect(lastRequestedPage(imageSpy)).toBe(2));
  });

  it("opens on the page holding the first open flag", async () => {
    const imageSpy = renderView(threePageDocument(3));

    await screen.findByText(/Page 3 of 3/);
    await waitFor(() => expect(lastRequestedPage(imageSpy)).toBe(3));
  });
});
