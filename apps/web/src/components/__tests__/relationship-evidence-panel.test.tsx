/**
 * Phase 15.1 — Vitest для RelationshipEvidencePanel.
 *
 * Покрытие:
 * - loads + renders supporting tab (default)
 * - switches to contradicting tab
 * - confidence badge tone matches score thresholds (green / amber / red)
 * - empty supporting → CTA "Add archive search" виден (disabled)
 * - empty contradicting → neutral message
 * - close button calls onClose
 *
 * fetchRelationshipEvidence мокается через vi.mock — без реального API call.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  RelationshipEvidencePanel,
  confidenceBadgeTone,
} from "@/components/relationship-evidence-panel";
import type { RelationshipEvidenceResponse } from "@/lib/relationships-api";
import enMessages from "../../../messages/en.json";

// ---- Mocks ----------------------------------------------------------------

type FetchEvidenceFn = typeof import("@/lib/relationships-api").fetchRelationshipEvidence;

const mockFetch = vi.fn<FetchEvidenceFn>();

vi.mock("@/lib/relationships-api", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/relationships-api")>("@/lib/relationships-api");
  return {
    ...actual,
    fetchRelationshipEvidence: (params: Parameters<FetchEvidenceFn>[0]) => mockFetch(params),
  };
});

// ---- Fixtures -------------------------------------------------------------

function makeResponse(
  overrides: Partial<RelationshipEvidenceResponse> = {},
): RelationshipEvidenceResponse {
  return {
    relationship: {
      kind: "spouse",
      subject_person_id: "p-a",
      object_person_id: "p-b",
    },
    supporting: [
      {
        source_id: "s-1",
        citation_id: "c-1",
        title: "Marriage record 1850",
        repository: "Lubelskie Archive",
        reliability: 0.8,
        citation: "vol 3 p. 17",
        snippet: "Sigmund married Anna",
        url: "https://archive.example/v3p17",
        added_at: "2026-04-01T00:00:00Z",
        kind: "citation",
        rule_id: null,
      },
    ],
    contradicting: [],
    confidence: {
      score: 0.92,
      method: "bayesian_fusion_v2",
      computed_at: "2026-05-01T00:00:00Z",
      hypothesis_id: "h-1",
    },
    provenance: {
      source_files: ["family.ged"],
      import_job_id: null,
      manual_edits: [],
    },
    ...overrides,
  };
}

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  return (
    <QueryClientProvider client={client}>
      <NextIntlClientProvider locale="en" messages={enMessages}>
        {children}
      </NextIntlClientProvider>
    </QueryClientProvider>
  );
}

const baseProps = {
  open: true,
  onClose: () => {},
  treeId: "t-1",
  kind: "spouse" as const,
  subjectId: "p-a",
  objectId: "p-b",
  subjectLabel: "Anna Smith",
  objectLabel: "John Smith",
};

beforeEach(() => {
  mockFetch.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

// ---- Tests ---------------------------------------------------------------

describe("confidenceBadgeTone", () => {
  it("returns green for score >= 0.85", () => {
    expect(confidenceBadgeTone(0.85)).toBe("green");
    expect(confidenceBadgeTone(0.99)).toBe("green");
  });

  it("returns amber for 0.6 <= score < 0.85", () => {
    expect(confidenceBadgeTone(0.6)).toBe("amber");
    expect(confidenceBadgeTone(0.84)).toBe("amber");
  });

  it("returns red for score < 0.6", () => {
    expect(confidenceBadgeTone(0.59)).toBe("red");
    expect(confidenceBadgeTone(0.0)).toBe("red");
  });
});

describe("RelationshipEvidencePanel", () => {
  it("renders supporting tab by default and shows source card", async () => {
    mockFetch.mockResolvedValueOnce(makeResponse());

    render(
      <Wrapper>
        <RelationshipEvidencePanel {...baseProps} />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("supporting-source-0")).toBeInTheDocument();
    });
    expect(screen.getByText("Marriage record 1850")).toBeInTheDocument();
    expect(screen.getByText("Lubelskie Archive")).toBeInTheDocument();
    expect(screen.getByTestId("evidence-subject-label")).toHaveTextContent("Anna Smith");
    expect(screen.getByTestId("evidence-object-label")).toHaveTextContent("John Smith");
  });

  it("switches to contradicting tab on click", async () => {
    mockFetch.mockResolvedValueOnce(
      makeResponse({
        contradicting: [
          {
            source_id: null,
            citation_id: null,
            title: "Inference rule: name_mismatch",
            repository: null,
            reliability: 0.4,
            citation: null,
            snippet: "Different surnames in the marriage record",
            url: null,
            added_at: "2026-04-01T00:00:00Z",
            kind: "inference_rule",
            rule_id: "name_mismatch",
          },
        ],
      }),
    );

    render(
      <Wrapper>
        <RelationshipEvidencePanel {...baseProps} />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("supporting-source-0")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("evidence-tab-contradicting"));

    await waitFor(() => {
      expect(screen.getByTestId("contradicting-source-0")).toBeInTheDocument();
    });
    expect(screen.getByText("Inference rule: name_mismatch")).toBeInTheDocument();
  });

  it("renders green confidence badge for score >= 0.85", async () => {
    mockFetch.mockResolvedValueOnce(
      makeResponse({
        confidence: {
          score: 0.92,
          method: "bayesian_fusion_v2",
          computed_at: "2026-05-01T00:00:00Z",
          hypothesis_id: "h-1",
        },
      }),
    );

    render(
      <Wrapper>
        <RelationshipEvidencePanel {...baseProps} />
      </Wrapper>,
    );

    await waitFor(() => {
      const badge = screen.getByTestId("confidence-badge");
      expect(badge).toHaveAttribute("data-tone", "green");
      expect(badge).toHaveAttribute("data-method", "bayesian_fusion_v2");
    });
  });

  it("renders red confidence badge for score < 0.6 + naive_count method", async () => {
    mockFetch.mockResolvedValueOnce(
      makeResponse({
        supporting: [],
        confidence: {
          score: 0.0,
          method: "naive_count",
          computed_at: "2026-05-01T00:00:00Z",
          hypothesis_id: null,
        },
      }),
    );

    render(
      <Wrapper>
        <RelationshipEvidencePanel {...baseProps} />
      </Wrapper>,
    );

    await waitFor(() => {
      const badge = screen.getByTestId("confidence-badge");
      expect(badge).toHaveAttribute("data-tone", "red");
      expect(badge).toHaveAttribute("data-method", "naive_count");
    });
  });

  it("shows empty-supporting CTA when supporting is empty", async () => {
    mockFetch.mockResolvedValueOnce(
      makeResponse({
        supporting: [],
        confidence: {
          score: 0.0,
          method: "naive_count",
          computed_at: "2026-05-01T00:00:00Z",
          hypothesis_id: null,
        },
      }),
    );

    render(
      <Wrapper>
        <RelationshipEvidencePanel {...baseProps} />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("empty-supporting")).toBeInTheDocument();
    });
    const cta = screen.getByTestId("add-archive-search-cta");
    expect(cta).toBeInTheDocument();
    expect(cta).toBeDisabled();
  });

  it("shows neutral empty state on contradicting tab when none", async () => {
    mockFetch.mockResolvedValueOnce(makeResponse());

    render(
      <Wrapper>
        <RelationshipEvidencePanel {...baseProps} />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("supporting-source-0")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("evidence-tab-contradicting"));

    await waitFor(() => {
      expect(screen.getByTestId("empty-contradicting")).toBeInTheDocument();
    });
  });

  it("calls onClose when close button is clicked", async () => {
    mockFetch.mockResolvedValueOnce(makeResponse());
    const onClose = vi.fn();

    render(
      <Wrapper>
        <RelationshipEvidencePanel {...baseProps} onClose={onClose} />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("evidence-close-button")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("evidence-close-button"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("does not render anything when open=false", () => {
    render(
      <Wrapper>
        <RelationshipEvidencePanel {...baseProps} open={false} />
      </Wrapper>,
    );

    expect(screen.queryByTestId("relationship-evidence-panel")).not.toBeInTheDocument();
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("renders provenance tab content when source_files present", async () => {
    mockFetch.mockResolvedValueOnce(
      makeResponse({
        provenance: {
          source_files: ["birth.ged", "marriage.ged"],
          import_job_id: "00000000-0000-0000-0000-000000000099",
          manual_edits: [],
        },
      }),
    );

    render(
      <Wrapper>
        <RelationshipEvidencePanel {...baseProps} />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("supporting-source-0")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("evidence-tab-provenance"));

    await waitFor(() => {
      expect(screen.getByTestId("provenance-content")).toBeInTheDocument();
    });
    expect(screen.getByText("birth.ged")).toBeInTheDocument();
    expect(screen.getByText("00000000-0000-0000-0000-000000000099")).toBeInTheDocument();
  });
});
