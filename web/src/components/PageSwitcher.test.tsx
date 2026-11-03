import { MantineProvider } from "@mantine/core";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PageSwitcher } from "./PageSwitcher";

function renderSwitcher(props: Partial<Parameters<typeof PageSwitcher>[0]> = {}) {
  const onChange = vi.fn();
  render(
    <MantineProvider>
      <PageSwitcher page={2} pageCount={3} onChange={onChange} {...props} />
    </MantineProvider>,
  );
  return onChange;
}

describe("PageSwitcher", () => {
  it("renders nothing for a single-page document", () => {
    renderSwitcher({ page: 1, pageCount: 1 });
    expect(screen.queryByRole("group", { name: "Page navigation" })).toBeNull();
  });

  it("shows the current page and moves to the previous and next page", () => {
    const onChange = renderSwitcher();
    expect(screen.getByText(/Page 2 of 3/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    fireEvent.click(screen.getByRole("button", { name: "Previous page" }));

    expect(onChange.mock.calls).toEqual([[3], [1]]);
  });

  it("disables previous on the first page and next on the last", () => {
    renderSwitcher({ page: 1, pageCount: 2 });
    expect((screen.getByRole("button", { name: "Previous page" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Next page" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("shows how many open flags are on the current page", () => {
    renderSwitcher({ openFlagsByPage: new Map([[2, 3]]) });
    expect(screen.getByText(/3 open flags/)).toBeTruthy();
  });
});
