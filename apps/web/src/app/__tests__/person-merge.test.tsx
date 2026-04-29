/**
 * Phase 6.4 — vitest для manual merge UI и merge-log.
 *
 * Покрывает:
 *   - submit формы вызывает commitMerge с правильным payload'ом;
 *   - в merge-log undo-кнопка disabled для записей вне 90-day окна;
 *   - merge-log render'ится без missing-key fallback'ов в обеих локалях.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import enMessages from "../../../messages/en.json";
import ruMessages from "../../../messages/ru.json";

// next/navigation hooks — должны быть замоканы до import'ов компонентов.
const mockSearchParams = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useParams: () => mockParams,
  useSearchParams: () => mockSearchParams,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const mockParams: Record<string, string> = {};

const apiMocks = vi.hoisted(() => ({
  fetchMergePreview: vi.fn(),
  commitMerge: vi.fn(),
  fetchMergeHistory: vi.fn(),
  undoMerge: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    fetchMergePreview: apiMocks.fetchMergePreview,
    commitMerge: apiMocks.commitMerge,
    fetchMergeHistory: apiMocks.fetchMergeHistory,
    undoMerge: apiMocks.undoMerge,
  };
});

import MergeLogPage from "@/app/persons/[id]/merge-log/page";
import ManualMergePage from "@/app/persons/merge/[primaryId]/page";

function withProviders(ui: ReactNode, locale: "en" | "ru" = "en") {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  const messages = locale === "en" ? enMessages : ruMessages;
  return render(
    <NextIntlClientProvider locale={locale} messages={messages}>
      <QueryClientProvider client={client}>{ui}</QueryClientProvider>
    </NextIntlClientProvider>,
  );
}

const PRIMARY_ID = "11111111-1111-1111-1111-111111111111";
const CANDIDATE_ID = "22222222-2222-2222-2222-222222222222";

beforeEach(() => {
  apiMocks.fetchMergePreview.mockReset();
  apiMocks.commitMerge.mockReset();
  apiMocks.fetchMergeHistory.mockReset();
  apiMocks.undoMerge.mockReset();
  for (const key of Array.from(mockSearchParams.keys())) {
    mockSearchParams.delete(key);
  }
  for (const key of Object.keys(mockParams)) delete mockParams[key];
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ManualMergePage — submit flow", () => {
  it("commits merge with survivor_choice derived from per-field picks", async () => {
    mockParams.primaryId = PRIMARY_ID;
    mockSearchParams.set("candidate", CANDIDATE_ID);

    apiMocks.fetchMergePreview.mockResolvedValue({
      survivor_id: PRIMARY_ID,
      merged_id: CANDIDATE_ID,
      default_survivor_id: PRIMARY_ID,
      fields: [
        {
          field: "birth_date",
          survivor_value: "1850-01-01",
          merged_value: "1851-03-12",
          after_merge_value: "1850-01-01",
        },
        {
          field: "death_date",
          survivor_value: null,
          merged_value: "1920-04-15",
          after_merge_value: "1920-04-15",
        },
      ],
      names: [],
      events: [],
      family_memberships: [],
      hypothesis_check: "no_hypotheses_found",
      conflicts: [],
    });
    apiMocks.commitMerge.mockResolvedValue({
      merge_id: "33333333-3333-3333-3333-333333333333",
      survivor_id: CANDIDATE_ID,
      merged_id: PRIMARY_ID,
      merged_at: new Date().toISOString(),
      confirm_token: "token",
    });

    const { container } = withProviders(<ManualMergePage />);

    await waitFor(() => {
      expect(container.querySelector('[data-field="birth_date"]')).not.toBeNull();
    });

    const user = userEvent.setup();
    // Pick "right" (candidate) for both fields → implied survivor = "right".
    const rightRadios = screen.getAllByLabelText(/Keep candidate/i);
    expect(rightRadios.length).toBe(2);
    const [firstRight, secondRight] = rightRadios;
    if (!firstRight || !secondRight) throw new Error("expected two candidate radios");
    await user.click(firstRight);
    await user.click(secondRight);

    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: /Confirm merge/i }));

    await waitFor(() => {
      expect(apiMocks.commitMerge).toHaveBeenCalledTimes(1);
    });
    const firstCall = apiMocks.commitMerge.mock.calls[0];
    if (!firstCall) throw new Error("commitMerge was not called");
    const [calledPersonId, payload] = firstCall;
    expect(calledPersonId).toBe(PRIMARY_ID);
    expect(payload).toMatchObject({
      target_id: CANDIDATE_ID,
      confirm: true,
      survivor_choice: "right",
    });
    expect(typeof payload.confirm_token).toBe("string");
    expect(payload.confirm_token.length).toBeGreaterThanOrEqual(8);
  });

  it("disables confirm button until reviewed checkbox is checked", async () => {
    mockParams.primaryId = PRIMARY_ID;
    mockSearchParams.set("candidate", CANDIDATE_ID);

    apiMocks.fetchMergePreview.mockResolvedValue({
      survivor_id: PRIMARY_ID,
      merged_id: CANDIDATE_ID,
      default_survivor_id: PRIMARY_ID,
      fields: [],
      names: [],
      events: [],
      family_memberships: [],
      hypothesis_check: "no_hypotheses_found",
      conflicts: [],
    });

    withProviders(<ManualMergePage />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Confirm merge/i })).toBeInTheDocument();
    });
    const button = screen.getByRole("button", { name: /Confirm merge/i });
    expect(button).toBeDisabled();

    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox"));
    expect(button).not.toBeDisabled();
  });

  it("shows missing-candidate message when ?candidate is absent", async () => {
    mockParams.primaryId = PRIMARY_ID;
    // no candidate query param

    withProviders(<ManualMergePage />);
    expect(screen.getByText(/No candidate selected/i)).toBeInTheDocument();
  });
});

describe("MergeLogPage — undo window", () => {
  const FIXED_NOW = new Date("2026-04-29T12:00:00Z");
  let dateNowSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    // Используем spy на Date.now() вместо useFakeTimers чтобы react-query
    // setTimeout/setInterval работали обычным образом и waitFor не таймаутил.
    dateNowSpy = vi.spyOn(Date, "now").mockImplementation(() => FIXED_NOW.getTime());
  });

  afterEach(() => {
    dateNowSpy?.mockRestore();
  });

  function makeItem(
    overrides: Partial<{
      merge_id: string;
      survivor_id: string;
      merged_id: string;
      merged_at: string;
      undone_at: string | null;
      purged_at: string | null;
    }>,
  ) {
    return {
      merge_id: overrides.merge_id ?? "merge-id",
      survivor_id: overrides.survivor_id ?? PRIMARY_ID,
      merged_id: overrides.merged_id ?? CANDIDATE_ID,
      merged_at: overrides.merged_at ?? FIXED_NOW.toISOString(),
      undone_at: overrides.undone_at ?? null,
      purged_at: overrides.purged_at ?? null,
    };
  }

  it("enables undo button for merges within the 90-day window", async () => {
    mockParams.id = PRIMARY_ID;

    apiMocks.fetchMergeHistory.mockResolvedValue({
      person_id: PRIMARY_ID,
      items: [
        makeItem({
          merge_id: "fresh-merge",
          merged_at: new Date(FIXED_NOW.getTime() - 5 * 24 * 60 * 60 * 1000).toISOString(),
        }),
      ],
    });

    withProviders(<MergeLogPage />);

    await waitFor(() => {
      expect(screen.getByTestId("undo-button")).toBeInTheDocument();
    });
    expect(screen.getByTestId("undo-button")).not.toBeDisabled();
  });

  it("disables undo button for merges past 90 days", async () => {
    mockParams.id = PRIMARY_ID;

    apiMocks.fetchMergeHistory.mockResolvedValue({
      person_id: PRIMARY_ID,
      items: [
        makeItem({
          merge_id: "stale-merge",
          merged_at: new Date(FIXED_NOW.getTime() - 100 * 24 * 60 * 60 * 1000).toISOString(),
        }),
      ],
    });

    withProviders(<MergeLogPage />);

    await waitFor(() => {
      expect(screen.getByTestId("undo-button")).toBeInTheDocument();
    });
    expect(screen.getByTestId("undo-button")).toBeDisabled();
    expect(screen.getByText(/window expired/i)).toBeInTheDocument();
  });

  it("disables undo button for already-undone merges", async () => {
    mockParams.id = PRIMARY_ID;

    apiMocks.fetchMergeHistory.mockResolvedValue({
      person_id: PRIMARY_ID,
      items: [
        makeItem({
          merge_id: "undone-merge",
          merged_at: FIXED_NOW.toISOString(),
          undone_at: new Date(FIXED_NOW.getTime() - 60 * 60 * 1000).toISOString(),
        }),
      ],
    });

    withProviders(<MergeLogPage />);

    await waitFor(() => {
      expect(screen.getByTestId("undo-button")).toBeInTheDocument();
    });
    expect(screen.getByTestId("undo-button")).toBeDisabled();
    expect(screen.getByText(/Undone at/i)).toBeInTheDocument();
  });

  it("renders empty state when person has no merges", async () => {
    mockParams.id = PRIMARY_ID;

    apiMocks.fetchMergeHistory.mockResolvedValue({
      person_id: PRIMARY_ID,
      items: [],
    });

    withProviders(<MergeLogPage />);

    await waitFor(() => {
      expect(screen.getByText(/hasn't taken part/i)).toBeInTheDocument();
    });
  });
});

describe("MergeLogPage — i18n parity", () => {
  it.each(["en", "ru"] as const)("renders %s without missing-key fallbacks", async (locale) => {
    mockParams.id = PRIMARY_ID;
    apiMocks.fetchMergeHistory.mockResolvedValue({
      person_id: PRIMARY_ID,
      items: [],
    });
    const { container } = withProviders(<MergeLogPage />, locale);
    await waitFor(() => {
      expect(container.querySelector("h1")?.textContent?.length ?? 0).toBeGreaterThan(0);
    });
    expect(container.innerHTML).not.toMatch(/\[missing:/i);
  });
});
