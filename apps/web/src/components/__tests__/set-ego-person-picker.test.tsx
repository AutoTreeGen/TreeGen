import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SetEgoPersonPicker } from "@/components/set-ego-person-picker";
import * as api from "@/lib/api";
import enMessages from "../../../messages/en.json";

/**
 * Phase 10.7a — vitest для SetEgoPersonPicker (ADR-0068).
 *
 * Покрыто:
 *   - non-owner (canEdit=false) видит disabled-state с tooltip-текстом.
 *   - search filters persons by name → renders results.
 *   - click on result calls setTreeOwnerPerson + onChange callback.
 */

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

const PERSON_SUMMARIES: api.PersonSummary[] = [
  {
    id: "person-1",
    gedcom_xref: null,
    sex: "M",
    confidence_score: 0.9,
    primary_name: "Vladimir Z",
    match_type: "substring",
  },
  {
    id: "person-2",
    gedcom_xref: null,
    sex: "F",
    confidence_score: 0.8,
    primary_name: "Vlada Z",
    match_type: "substring",
  },
];

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SetEgoPersonPicker", () => {
  it("renders disabled state with non-owner hint when canEdit=false", () => {
    render(
      wrap(<SetEgoPersonPicker treeId="tree-1" currentOwnerPersonId={null} canEdit={false} />),
    );

    expect(screen.getByText(/Yourself in this tree/i)).toBeInTheDocument();
    expect(screen.getByText(/Only the tree owner/i)).toBeInTheDocument();
    // Кнопки picker'а не должны быть видимы для non-owner.
    expect(screen.queryByText(/Pick a person/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Change$/)).not.toBeInTheDocument();
  });

  it("shows empty state and lets owner open the picker", () => {
    render(wrap(<SetEgoPersonPicker treeId="tree-1" currentOwnerPersonId={null} canEdit={true} />));
    // Empty-state CTA from i18n catalog.
    expect(screen.getByText(/You haven't set yourself in this tree yet/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Pick a person/i })).toBeInTheDocument();
  });

  it("filters persons by name and triggers PATCH on selection", async () => {
    const searchSpy = vi.spyOn(api, "searchPersons").mockResolvedValue({
      tree_id: "tree-1",
      total: PERSON_SUMMARIES.length,
      limit: 20,
      offset: 0,
      items: PERSON_SUMMARIES,
    });
    const setSpy = vi
      .spyOn(api, "setTreeOwnerPerson")
      .mockResolvedValue({ tree_id: "tree-1", owner_person_id: "person-1" });
    const onChange = vi.fn();

    render(
      wrap(
        <SetEgoPersonPicker
          treeId="tree-1"
          currentOwnerPersonId={null}
          canEdit={true}
          onChange={onChange}
        />,
      ),
    );

    // Открываем picker.
    fireEvent.click(screen.getByRole("button", { name: /Pick a person/i }));
    // Печатаем 2+ символа — search активируется.
    const input = await screen.findByTestId("ego-anchor-search-input");
    fireEvent.change(input, { target: { value: "Vl" } });

    await waitFor(() => {
      expect(searchSpy).toHaveBeenCalledWith("tree-1", { q: "Vl", limit: 20 });
    });
    const result = await screen.findByTestId("ego-anchor-result-person-1");
    expect(result).toHaveTextContent(/Vladimir Z/);

    fireEvent.click(result);

    await waitFor(() => {
      expect(setSpy).toHaveBeenCalledWith("tree-1", "person-1");
    });
    await waitFor(() => {
      expect(onChange).toHaveBeenCalledWith({
        tree_id: "tree-1",
        owner_person_id: "person-1",
      });
    });
  });
});
