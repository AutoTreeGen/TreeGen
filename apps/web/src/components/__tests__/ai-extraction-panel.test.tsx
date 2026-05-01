import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AIExtractionPanel } from "@/components/ai-extraction-panel";
import * as api from "@/lib/api";
import enMessages from "../../../messages/en.json";

/**
 * Phase 10.2b — vitest для AIExtractionPanel.
 *
 * Покрыто:
 *   - renderless (no extraction yet) → "no extraction" сообщение.
 *   - completed run → fact_count + cost badge.
 *   - submit с file → postAIExtractVision вызван.
 *   - 429 → errorBudget i18n string.
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

const NO_EXTRACTION_STATUS: api.AIExtractStatusResponse = {
  source_id: "src-1",
  has_extraction: false,
  extraction: null,
  fact_count: 0,
  cost_usd: 0,
  budget_remaining_runs: 9,
  budget_remaining_tokens: 95_000,
  extract_budget_usd: 0.5,
};

const COMPLETED_STATUS: api.AIExtractStatusResponse = {
  source_id: "src-1",
  has_extraction: true,
  extraction: {
    id: "ext-1",
    source_id: "src-1",
    tree_id: "tree-1",
    requested_by_user_id: "user-1",
    model_version: "claude-sonnet-4-6",
    prompt_version: "source_extractor_v1",
    status: "completed",
    input_tokens: 500,
    output_tokens: 200,
    error: null,
    created_at: "2026-04-30T12:00:00Z",
    completed_at: "2026-04-30T12:00:05Z",
  },
  fact_count: 3,
  cost_usd: 0.0045,
  budget_remaining_runs: 9,
  budget_remaining_tokens: 95_000,
  extract_budget_usd: 0.5,
};

if (!COMPLETED_STATUS.extraction) {
  throw new Error("test fixture invariant broken: COMPLETED_STATUS must include extraction");
}
const SUCCESS_VISION: api.AIExtractVisionResponse = {
  extraction: COMPLETED_STATUS.extraction,
  fact_count: 3,
  budget_remaining_runs: 8,
  budget_remaining_tokens: 94_500,
  estimated_cost_usd: 0.012,
  image_was_resized: true,
  image_was_rotated: false,
  image_original_bytes: 5_242_880,
  image_processed_bytes: 819_200,
};

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("AIExtractionPanel", () => {
  it("renders 'no extraction yet' when status returns has_extraction=false", async () => {
    vi.spyOn(api, "fetchAIExtractStatus").mockResolvedValue(NO_EXTRACTION_STATUS);
    render(wrap(<AIExtractionPanel sourceId="src-1" />));

    await waitFor(() => {
      expect(screen.getByText(/No AI extraction has run/i)).toBeInTheDocument();
    });
    // Per-source cap displayed in budget block.
    const budget = screen.getByTestId("ai-extract-budget");
    expect(budget.textContent).toContain("$0.50");
  });

  it("renders 'completed' status with fact count and cost", async () => {
    vi.spyOn(api, "fetchAIExtractStatus").mockResolvedValue(COMPLETED_STATUS);
    render(wrap(<AIExtractionPanel sourceId="src-1" />));

    await waitFor(() => {
      expect(screen.getByText(/3 new fact suggestions/)).toBeInTheDocument();
    });
    // Cost label (4-decimal $).
    expect(screen.getByText(/Cost: \$0.0045/)).toBeInTheDocument();
  });

  it("posts the file to vision endpoint when submitted", async () => {
    vi.spyOn(api, "fetchAIExtractStatus").mockResolvedValue(NO_EXTRACTION_STATUS);
    const post = vi.spyOn(api, "postAIExtractVision").mockResolvedValue(SUCCESS_VISION);

    render(wrap(<AIExtractionPanel sourceId="src-1" />));

    const input = (await screen.findByTestId("ai-extract-file-input")) as HTMLInputElement;
    const submit = await screen.findByTestId("ai-extract-submit");
    const file = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], "scan.png", {
      type: "image/png",
    });
    fireEvent.change(input, { target: { files: [file] } });
    expect((submit as HTMLButtonElement).disabled).toBe(false);

    fireEvent.click(submit);
    await waitFor(() => {
      expect(post).toHaveBeenCalledWith("src-1", file);
    });
    // Image-optimization notice показывается после успеха (resized=true).
    await waitFor(() => {
      expect(screen.getByTestId("ai-extract-image-notice")).toBeInTheDocument();
    });
  });

  it("renders the budget-error message on 429 from vision endpoint", async () => {
    vi.spyOn(api, "fetchAIExtractStatus").mockResolvedValue(NO_EXTRACTION_STATUS);
    vi.spyOn(api, "postAIExtractVision").mockRejectedValue(
      new api.ApiError(429, "AI budget exceeded: cost_per_source_usd_x10000=4321 >= limit=5000"),
    );

    render(wrap(<AIExtractionPanel sourceId="src-1" />));

    const input = (await screen.findByTestId("ai-extract-file-input")) as HTMLInputElement;
    const submit = await screen.findByTestId("ai-extract-submit");
    const file = new File([new Uint8Array([0xff, 0xd8, 0xff])], "tiny.jpg", {
      type: "image/jpeg",
    });
    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.click(submit);

    await waitFor(() => {
      const alert = screen.getByRole("alert");
      expect(alert.textContent).toMatch(/limit/i);
    });
  });
});
