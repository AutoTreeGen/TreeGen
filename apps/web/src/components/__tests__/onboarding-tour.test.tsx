/**
 * Phase 4.15 — Vitest для OnboardingTour + RestartTourButton.
 *
 * Покрывает:
 * - Tour ПОКАЗЫВАЕТСЯ on first visit (mock Clerk user с tour_completed=false)
 * - Tour НЕ показывается after completed
 * - Skip persists в Clerk metadata и не показывает повторно при re-mount
 * - Restart tour button сбрасывает persistence и редиректит на dashboard
 *
 * Clerk-хук мокается; usePathname мокается — мы хотим контролировать
 * текущий маршрут, чтобы тестировать auto-trigger именно на /dashboard.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import enMessages from "../../../messages/en.json";

// ---- Mocks ------------------------------------------------------------------

type MockClerkUser = {
  unsafeMetadata: Record<string, unknown>;
  update: ReturnType<typeof vi.fn>;
};

const mockClerkUser: MockClerkUser = {
  unsafeMetadata: {},
  update: vi.fn().mockResolvedValue({}),
};

let mockPathname = "/dashboard";

vi.mock("@clerk/nextjs", () => ({
  useUser: () => ({ user: mockClerkUser, isLoaded: true }),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => mockPathname,
}));

import { OnboardingTour, RestartTourButton } from "@/components/onboarding-tour";

function wrapper({ children }: { children: ReactNode }) {
  return (
    <NextIntlClientProvider locale="en" messages={enMessages}>
      {children}
    </NextIntlClientProvider>
  );
}

beforeEach(() => {
  mockClerkUser.unsafeMetadata = {};
  mockClerkUser.update.mockReset().mockResolvedValue({});
  mockPathname = "/dashboard";
  // Сброс query-string между тестами (RestartTourButton + auto-trigger зависят
  // от ?restartTour=1).
  window.history.replaceState({}, "", "/dashboard");
});

afterEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Auto-trigger logic
// ---------------------------------------------------------------------------

describe("OnboardingTour — auto-trigger", () => {
  it("shows on first visit (no tour state in metadata)", async () => {
    render(<OnboardingTour />, { wrapper });
    await waitFor(() => {
      expect(screen.getByTestId("onboarding-tour")).toBeInTheDocument();
    });
    // Step 1 = welcome
    expect(screen.getByTestId("onboarding-tour-step-label")).toHaveTextContent("Step 1 of 7");
    expect(screen.getByText(/Welcome to AutoTreeGen/)).toBeInTheDocument();
  });

  it("does NOT show after tour_completed=true", () => {
    mockClerkUser.unsafeMetadata = {
      tour: { tour_completed: true, tour_completed_at: "2026-04-01T00:00:00Z" },
    };
    render(<OnboardingTour />, { wrapper });
    expect(screen.queryByTestId("onboarding-tour")).not.toBeInTheDocument();
  });

  it("does NOT show after tour_skipped=true", () => {
    mockClerkUser.unsafeMetadata = { tour: { tour_skipped: true } };
    render(<OnboardingTour />, { wrapper });
    expect(screen.queryByTestId("onboarding-tour")).not.toBeInTheDocument();
  });

  it("does NOT auto-show outside /dashboard", () => {
    mockPathname = "/persons";
    render(<OnboardingTour />, { wrapper });
    expect(screen.queryByTestId("onboarding-tour")).not.toBeInTheDocument();
  });

  it("DOES show with ?restartTour=1 even after completed", async () => {
    mockClerkUser.unsafeMetadata = { tour: { tour_completed: true } };
    mockPathname = "/dashboard";
    window.history.replaceState({}, "", "/dashboard?restartTour=1");
    render(<OnboardingTour />, { wrapper });
    await waitFor(() => {
      expect(screen.getByTestId("onboarding-tour")).toBeInTheDocument();
    });
  });
});

// ---------------------------------------------------------------------------
// Step navigation
// ---------------------------------------------------------------------------

describe("OnboardingTour — step navigation", () => {
  it("advances to step 2 on Next click", async () => {
    render(<OnboardingTour />, { wrapper });
    await waitFor(() => screen.getByTestId("onboarding-tour"));
    fireEvent.click(screen.getByTestId("onboarding-tour-next"));
    expect(screen.getByTestId("onboarding-tour-step-label")).toHaveTextContent("Step 2 of 7");
  });

  it("Back is disabled on first step", async () => {
    render(<OnboardingTour />, { wrapper });
    await waitFor(() => screen.getByTestId("onboarding-tour"));
    expect(screen.getByTestId("onboarding-tour-back")).toBeDisabled();
  });

  it("shows Finish on last step instead of Next", async () => {
    render(<OnboardingTour />, { wrapper });
    await waitFor(() => screen.getByTestId("onboarding-tour"));
    // Click Next 6 times (7 steps total → final step has Finish)
    for (let i = 0; i < 6; i += 1) {
      fireEvent.click(screen.getByTestId("onboarding-tour-next"));
    }
    expect(screen.getByTestId("onboarding-tour-step-label")).toHaveTextContent("Step 7 of 7");
    expect(screen.queryByTestId("onboarding-tour-next")).not.toBeInTheDocument();
    expect(screen.getByTestId("onboarding-tour-finish")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Persistence: skip / finish
// ---------------------------------------------------------------------------

describe("OnboardingTour — persistence", () => {
  it("Skip writes tour_skipped=true to Clerk metadata", async () => {
    render(<OnboardingTour />, { wrapper });
    await waitFor(() => screen.getByTestId("onboarding-tour"));
    fireEvent.click(screen.getByTestId("onboarding-tour-skip"));
    await waitFor(() => {
      expect(mockClerkUser.update).toHaveBeenCalledWith({
        unsafeMetadata: expect.objectContaining({
          tour: expect.objectContaining({ tour_skipped: true, tour_completed: false }),
        }),
      });
    });
  });

  it("Finish writes tour_completed=true with timestamp", async () => {
    render(<OnboardingTour />, { wrapper });
    await waitFor(() => screen.getByTestId("onboarding-tour"));
    for (let i = 0; i < 6; i += 1) {
      fireEvent.click(screen.getByTestId("onboarding-tour-next"));
    }
    fireEvent.click(screen.getByTestId("onboarding-tour-finish"));
    await waitFor(() => {
      expect(mockClerkUser.update).toHaveBeenCalledWith({
        unsafeMetadata: expect.objectContaining({
          tour: expect.objectContaining({
            tour_completed: true,
            tour_skipped: false,
            tour_completed_at: expect.stringMatching(/\d{4}-\d{2}-\d{2}T/),
          }),
        }),
      });
    });
  });

  it("after Skip, re-mount with skipped state does NOT reopen", async () => {
    const { unmount } = render(<OnboardingTour />, { wrapper });
    await waitFor(() => screen.getByTestId("onboarding-tour"));
    fireEvent.click(screen.getByTestId("onboarding-tour-skip"));
    await waitFor(() => expect(mockClerkUser.update).toHaveBeenCalled());
    // Симулируем persistence: applies metadata patch к нашему mock-объекту
    mockClerkUser.unsafeMetadata = { tour: { tour_skipped: true } };
    unmount();

    render(<OnboardingTour />, { wrapper });
    expect(screen.queryByTestId("onboarding-tour")).not.toBeInTheDocument();
  });

  it("Close button hides overlay without persisting (single-session dismiss)", async () => {
    render(<OnboardingTour />, { wrapper });
    await waitFor(() => screen.getByTestId("onboarding-tour"));
    fireEvent.click(screen.getByTestId("onboarding-tour-close"));
    await waitFor(() => {
      expect(screen.queryByTestId("onboarding-tour")).not.toBeInTheDocument();
    });
    expect(mockClerkUser.update).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// RestartTourButton
// ---------------------------------------------------------------------------

describe("RestartTourButton", () => {
  it("clears persistence and navigates to /dashboard?restartTour=1", async () => {
    mockClerkUser.unsafeMetadata = {
      tour: { tour_completed: true, tour_completed_at: "2026-04-01T00:00:00Z" },
    };
    const navigate = vi.fn();
    render(<RestartTourButton navigate={navigate} />, { wrapper });
    fireEvent.click(screen.getByTestId("restart-tour"));
    await waitFor(() => {
      expect(mockClerkUser.update).toHaveBeenCalledWith({
        unsafeMetadata: expect.objectContaining({
          tour: expect.objectContaining({
            tour_completed: false,
            tour_skipped: false,
            tour_completed_at: null,
          }),
        }),
      });
    });
    expect(navigate).toHaveBeenCalledWith("/dashboard?restartTour=1");
  });
});
