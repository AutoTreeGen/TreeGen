import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";

import { GlobalErrorBoundary, SectionErrorBoundary } from "@/components/error-boundary";
import enMessages from "../../messages/en.json";

function withIntl(node: React.ReactNode) {
  return (
    <NextIntlClientProvider locale="en" messages={enMessages}>
      {node}
    </NextIntlClientProvider>
  );
}

function Boom({ shouldThrow }: { shouldThrow: boolean }) {
  if (shouldThrow) {
    throw new Error("kaboom");
  }
  return <p>kid renders fine</p>;
}

describe("GlobalErrorBoundary", () => {
  it("renders children when no error", () => {
    render(
      withIntl(
        <GlobalErrorBoundary>
          <p>hello</p>
        </GlobalErrorBoundary>,
      ),
    );
    expect(screen.getByText("hello")).toBeInTheDocument();
  });

  it("catches errors and shows fallback UI with mailto and reset", async () => {
    // react silently logs the boundary catch; suppress for noisy CI.
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      withIntl(
        <GlobalErrorBoundary>
          <Boom shouldThrow />
        </GlobalErrorBoundary>,
      ),
    );

    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText(/Something went wrong/i)).toBeInTheDocument();
    expect(screen.getByText(/kaboom/)).toBeInTheDocument();

    const reportLink = screen.getByText(/Report issue/i);
    expect(reportLink).toHaveAttribute(
      "href",
      expect.stringMatching(/^mailto:support@autotreegen\.com/),
    );

    const tryAgain = screen.getByRole("button", { name: /Try again/i });
    expect(tryAgain).toBeInTheDocument();
    await userEvent.click(tryAgain);
    consoleError.mockRestore();
  });
});

describe("SectionErrorBoundary", () => {
  it("uses section-level title (not global)", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      withIntl(
        <SectionErrorBoundary>
          <Boom shouldThrow />
        </SectionErrorBoundary>,
      ),
    );
    expect(screen.getByText(/This section couldn't load/i)).toBeInTheDocument();
    // global title MUST NOT be present (это inline section, не whole page).
    expect(screen.queryByText(/^Something went wrong$/i)).not.toBeInTheDocument();
    consoleError.mockRestore();
  });
});
