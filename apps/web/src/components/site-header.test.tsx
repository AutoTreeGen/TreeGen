import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import enMessages from "../../messages/en.json";

/**
 * Phase 11.1 — vitest для tree-picker логики в `<SiteHeader>`.
 *
 * Покрыто:
 *   - 0 trees → trigger-кнопка не рендерится.
 *   - ≥1 tree → trigger показывает имя current'а.
 *   - last_active_at сортирует первым (внутри dropdown'а).
 */

let mockTrees: import("@/lib/user-trees").UserTreeSummary[] = [];

vi.mock("@/lib/user-trees", async () => {
  const actual = await vi.importActual<typeof import("@/lib/user-trees")>("@/lib/user-trees");
  return {
    ...actual,
    fetchUserTrees: () => Promise.resolve({ items: mockTrees }),
    readCurrentTreeId: () => "tree-1",
    writeCurrentTreeId: vi.fn(),
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import { TreePicker } from "@/components/tree-picker";

function wrap(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return (
    <NextIntlClientProvider locale="en" messages={enMessages}>
      <QueryClientProvider client={client}>{ui}</QueryClientProvider>
    </NextIntlClientProvider>
  );
}

beforeEach(() => {
  mockTrees = [];
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("TreePicker", () => {
  it("renders nothing when user has zero trees", async () => {
    mockTrees = [];
    const { container } = render(wrap(<TreePicker />));
    // The query resolves async; once it does, dropdown stays unmounted.
    await waitFor(() => {
      expect(container.querySelector('[data-testid="tree-picker-trigger"]')).toBeNull();
    });
  });

  it("renders trigger with current tree name when user has trees", async () => {
    mockTrees = [
      {
        id: "tree-1",
        name: "Smith Family",
        role: "owner",
        last_active_at: "2026-04-29T10:00:00Z",
      },
      {
        id: "tree-2",
        name: "Cooper Family",
        role: "editor",
        last_active_at: "2026-03-01T10:00:00Z",
      },
    ];
    render(wrap(<TreePicker />));

    const trigger = await screen.findByTestId("tree-picker-trigger");
    expect(trigger).toBeInTheDocument();
    await waitFor(() => {
      expect(trigger.textContent).toContain("Smith Family");
    });
  });

  it("opens dropdown on click and lists trees in last-active-first order", async () => {
    mockTrees = [
      {
        id: "tree-old",
        name: "Old Tree",
        role: "viewer",
        last_active_at: "2026-01-01T10:00:00Z",
      },
      {
        id: "tree-recent",
        name: "Recent Tree",
        role: "editor",
        last_active_at: "2026-04-29T10:00:00Z",
      },
    ];
    render(wrap(<TreePicker />));

    const trigger = await screen.findByTestId("tree-picker-trigger");
    trigger.click();

    const items = await screen.findAllByRole("menuitem");
    // Two trees + Manage trees = 2 menuitems for trees, 1 link for Manage.
    expect(items.length).toBe(2);
    expect(items[0]?.textContent).toContain("Recent Tree");
    expect(items[1]?.textContent).toContain("Old Tree");
  });

  it("dropdown contains a Manage trees link to /dashboard", async () => {
    mockTrees = [
      {
        id: "tree-1",
        name: "Solo",
        role: "owner",
        last_active_at: null,
      },
    ];
    render(wrap(<TreePicker />));

    const trigger = await screen.findByTestId("tree-picker-trigger");
    trigger.click();

    const link = await screen.findByRole("link", { name: "Manage trees" });
    expect(link).toHaveAttribute("href", "/dashboard");
  });
});
