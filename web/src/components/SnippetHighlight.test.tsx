import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SnippetHighlight } from "./SnippetHighlight";

describe("SnippetHighlight", () => {
  it("renders matched terms as mark and leaves the rest as text", () => {
    render(<SnippetHighlight snippet="Paid to <b>John Smith</b> in Bombay" />);
    expect(screen.getByText("John Smith").tagName).toBe("MARK");
    expect(screen.getByText(/Paid to/)).toBeTruthy();
    expect(screen.getByText(/in Bombay/)).toBeTruthy();
  });

  it("does not interpret extra HTML from OCR text", () => {
    const { container } = render(
      <SnippetHighlight snippet={'Amount <b>$10</b> <img src=x onerror="alert(1)">'} />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("$10").tagName).toBe("MARK");
  });
});
