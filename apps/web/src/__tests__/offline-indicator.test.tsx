import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OfflineIndicator } from "@/components/offline-indicator";
import enMessages from "../../messages/en.json";

function renderWithProviders(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <NextIntlClientProvider locale="en" messages={enMessages}>
        {node}
      </NextIntlClientProvider>
    </QueryClientProvider>,
  );
}

describe("OfflineIndicator", () => {
  let originalOnLine: PropertyDescriptor | undefined;

  beforeEach(() => {
    originalOnLine = Object.getOwnPropertyDescriptor(navigator, "onLine");
  });

  afterEach(() => {
    if (originalOnLine) {
      Object.defineProperty(navigator, "onLine", originalOnLine);
    }
  });

  function setOnline(value: boolean) {
    Object.defineProperty(navigator, "onLine", { value, configurable: true });
  }

  it("renders nothing when navigator is online", () => {
    setOnline(true);
    renderWithProviders(<OfflineIndicator />);
    expect(screen.queryByTestId("offline-banner")).not.toBeInTheDocument();
  });

  it("shows banner when offline event fires", () => {
    setOnline(true);
    renderWithProviders(<OfflineIndicator />);
    act(() => {
      setOnline(false);
      window.dispatchEvent(new Event("offline"));
    });
    expect(screen.getByTestId("offline-banner")).toBeInTheDocument();
    expect(screen.getByText(/offline/i)).toBeInTheDocument();
  });

  it("hides banner and invalidates queries on online event", () => {
    setOnline(false);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidate = vi.spyOn(qc, "invalidateQueries");
    render(
      <QueryClientProvider client={qc}>
        <NextIntlClientProvider locale="en" messages={enMessages}>
          <OfflineIndicator />
        </NextIntlClientProvider>
      </QueryClientProvider>,
    );
    expect(screen.getByTestId("offline-banner")).toBeInTheDocument();

    act(() => {
      setOnline(true);
      window.dispatchEvent(new Event("online"));
    });
    expect(screen.queryByTestId("offline-banner")).not.toBeInTheDocument();
    expect(invalidate).toHaveBeenCalled();
  });
});
