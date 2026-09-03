import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../api/client";
import { LoginPage } from "./LoginPage";

function renderLoginPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider>
      <QueryClientProvider client={queryClient}>
        <LoginPage />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

describe("LoginPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("submits the trimmed reviewer name and password", async () => {
    const loginSpy = vi.spyOn(api, "login").mockResolvedValue({ reviewer: "paul" });

    renderLoginPage();
    fireEvent.change(screen.getByLabelText(/Reviewer/), { target: { value: "  paul  " } });
    fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(loginSpy).toHaveBeenCalledWith("paul", "secret"));
  });

  it("shows an explicit 'invalid password' message on a 401, not a generic failure", async () => {
    vi.spyOn(api, "login").mockRejectedValue(new ApiError(401, { detail: "invalid credentials" }));

    renderLoginPage();
    fireEvent.change(screen.getByLabelText(/Reviewer/), { target: { value: "paul" } });
    fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("Invalid password")).toBeTruthy();
  });

  it("shows a generic failure message for a non-401 error", async () => {
    vi.spyOn(api, "login").mockRejectedValue(new ApiError(500, { detail: "boom" }));

    renderLoginPage();
    fireEvent.change(screen.getByLabelText(/Reviewer/), { target: { value: "paul" } });
    fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: "whatever" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("Sign-in failed")).toBeTruthy();
  });

  it("disables the submit button until both fields are filled", () => {
    renderLoginPage();
    const signInButton = () => screen.getByRole("button", { name: "Sign in" }) as HTMLButtonElement;
    expect(signInButton().disabled).toBe(true);

    fireEvent.change(screen.getByLabelText(/Reviewer/), { target: { value: "paul" } });
    expect(signInButton().disabled).toBe(true);

    fireEvent.change(screen.getByLabelText(/^Password/), { target: { value: "secret" } });
    expect(signInButton().disabled).toBe(false);
  });
});
