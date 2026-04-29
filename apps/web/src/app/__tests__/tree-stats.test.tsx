/**
 * Phase 6.5 — vitest для tree-stats UI.
 *
 * Покрывает: render с mocked API (counts + top surnames + oldest year),
 * empty-state for surnames + oldest, error/retry, i18n parity (en + ru).
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import enMessages from "../../../messages/en.json";
import ruMessages from "../../../messages/ru.json";

const mockParams: Record<string, string> = {};

vi.mock("next/navigation", () => ({
  useParams: () => mockParams,
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const apiMocks = vi.hoisted(() => ({
  fetchTreeStatistics: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    fetchTreeStatistics: apiMocks.fetchTreeStatistics,
  };
});

import TreeStatsPage from "@/app/trees/[id]/stats/page";

const TREE_ID = "11111111-1111-1111-1111-111111111111";

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

beforeEach(() => {
  apiMocks.fetchTreeStatistics.mockReset();
  for (const key of Object.keys(mockParams)) delete mockParams[key];
  mockParams.id = TREE_ID;
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("TreeStatsPage — render with data", () => {
  const fullStats = {
    tree_id: TREE_ID,
    persons_count: 7,
    families_count: 3,
    events_count: 7,
    sources_count: 2,
    hypotheses_count: 5,
    dna_matches_count: 1,
    places_count: 4,
    pedigree_max_depth: 3,
    oldest_birth_year: 1800,
    top_surnames: [
      { surname: "First", person_count: 4 },
      { surname: "Second", person_count: 3 },
    ],
  };

  it("renders all 8 stat cards with correct counts", async () => {
    apiMocks.fetchTreeStatistics.mockResolvedValue(fullStats);

    withProviders(<TreeStatsPage />);

    await waitFor(() => {
      expect(screen.getAllByTestId("stat-card")).toHaveLength(8);
    });

    // Each card pairs a label and a number; pull the cards out and assert
    // their numeric content one-to-one.
    const cards = screen.getAllByTestId("stat-card");
    const numbers = cards.map((card) => card.querySelector("p.tabular-nums")?.textContent);
    // Order in component: persons, families, events, sources, hypotheses,
    // dnaMatches, places, pedigreeDepth.
    expect(numbers).toEqual(["7", "3", "7", "2", "5", "1", "4", "3"]);
  });

  it("renders oldest birth year", async () => {
    apiMocks.fetchTreeStatistics.mockResolvedValue(fullStats);

    withProviders(<TreeStatsPage />);

    await waitFor(() => {
      expect(screen.getByTestId("oldest-year")).toBeInTheDocument();
    });
    expect(screen.getByTestId("oldest-year").textContent).toBe("1800");
  });

  it("renders top surnames bar chart with relative widths", async () => {
    apiMocks.fetchTreeStatistics.mockResolvedValue(fullStats);

    const { container } = withProviders(<TreeStatsPage />);

    await waitFor(() => {
      expect(screen.getByTestId("surnames-list")).toBeInTheDocument();
    });

    const items = container.querySelectorAll("[data-testid='surnames-list'] li");
    expect(items).toHaveLength(2);
    // First (4) is the largest, gets 100%; Second (3) gets 75%. Bar fill =
    // first <div> nested inside the aria-hidden bar wrapper (one per item).
    const bars = container.querySelectorAll("[data-testid='surnames-list'] [aria-hidden='true']");
    expect(bars).toHaveLength(2);
    const firstBarFill = (bars[0]?.firstElementChild as HTMLElement | null)?.style.width;
    const secondBarFill = (bars[1]?.firstElementChild as HTMLElement | null)?.style.width;
    expect(firstBarFill).toBe("100%");
    expect(secondBarFill).toBe("75%");
  });
});

describe("TreeStatsPage — empty state", () => {
  it("shows 'no birth data' when oldest_birth_year is null", async () => {
    apiMocks.fetchTreeStatistics.mockResolvedValue({
      tree_id: TREE_ID,
      persons_count: 0,
      families_count: 0,
      events_count: 0,
      sources_count: 0,
      hypotheses_count: 0,
      dna_matches_count: 0,
      places_count: 0,
      pedigree_max_depth: 0,
      oldest_birth_year: null,
      top_surnames: [],
    });

    withProviders(<TreeStatsPage />);

    await waitFor(() => {
      expect(screen.getByText(/no birth-year/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/no surname/i)).toBeInTheDocument();
    expect(screen.queryByTestId("oldest-year")).toBeNull();
    expect(screen.queryByTestId("surnames-list")).toBeNull();
  });
});

describe("TreeStatsPage — error state", () => {
  it("shows error message and retry button on API failure", async () => {
    apiMocks.fetchTreeStatistics.mockRejectedValueOnce(new Error("boom"));

    withProviders(<TreeStatsPage />);

    await waitFor(() => {
      expect(screen.getByText(/Couldn't load statistics/i)).toBeInTheDocument();
    });
    const retryButton = screen.getByRole("button", { name: /Try again/i });

    // Second call resolves — clicking retry should fetch again.
    apiMocks.fetchTreeStatistics.mockResolvedValueOnce({
      tree_id: TREE_ID,
      persons_count: 1,
      families_count: 0,
      events_count: 0,
      sources_count: 0,
      hypotheses_count: 0,
      dna_matches_count: 0,
      places_count: 0,
      pedigree_max_depth: 0,
      oldest_birth_year: null,
      top_surnames: [],
    });
    const user = userEvent.setup();
    await user.click(retryButton);

    await waitFor(() => {
      expect(apiMocks.fetchTreeStatistics).toHaveBeenCalledTimes(2);
    });
  });
});

describe("TreeStatsPage — i18n parity", () => {
  it.each(["en", "ru"] as const)("renders %s without missing-key fallbacks", async (locale) => {
    apiMocks.fetchTreeStatistics.mockResolvedValue({
      tree_id: TREE_ID,
      persons_count: 0,
      families_count: 0,
      events_count: 0,
      sources_count: 0,
      hypotheses_count: 0,
      dna_matches_count: 0,
      places_count: 0,
      pedigree_max_depth: 0,
      oldest_birth_year: null,
      top_surnames: [],
    });
    const { container } = withProviders(<TreeStatsPage />, locale);
    await waitFor(() => {
      expect(container.querySelector("h1")?.textContent?.length ?? 0).toBeGreaterThan(0);
    });
    expect(container.innerHTML).not.toMatch(/\[missing:/i);
  });
});
