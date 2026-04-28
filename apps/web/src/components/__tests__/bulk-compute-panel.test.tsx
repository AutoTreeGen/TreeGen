import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BulkComputePanel } from "@/components/bulk-compute-panel";
import * as api from "@/lib/api";

// ---------------------------------------------------------------------------
// MockEventSource — повторяет sse.test.ts; держим локальную копию, чтобы
// не зависеть от внутреннего модуля и не плодить дополнительный helper-файл.
// ---------------------------------------------------------------------------

class MockEventSource {
  static CONNECTING = 0 as const;
  static OPEN = 1 as const;
  static CLOSED = 2 as const;

  static instances: MockEventSource[] = [];

  url: string;
  readyState: number = MockEventSource.CONNECTING;
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  open(): void {
    this.readyState = MockEventSource.OPEN;
    this.onopen?.(new Event("open"));
  }

  message(payload: unknown): void {
    const ev = new MessageEvent("message", { data: JSON.stringify(payload) });
    this.onmessage?.(ev);
  }

  close(): void {
    this.readyState = MockEventSource.CLOSED;
  }
}

beforeEach(() => {
  MockEventSource.instances = [];
  (globalThis as unknown as { EventSource: typeof MockEventSource }).EventSource = MockEventSource;
});

afterEach(() => {
  vi.restoreAllMocks();
});

function renderWithClient(node: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>);
}

const TREE_ID = "11111111-1111-1111-1111-111111111111";
const JOB_ID = "22222222-2222-2222-2222-222222222222";

function makeQueuedJob(): api.HypothesisComputeJobResponse {
  return {
    id: JOB_ID,
    tree_id: TREE_ID,
    status: "queued",
    rule_ids: null,
    progress: { processed: 0, total: 0, hypotheses_created: 0 },
    cancel_requested: false,
    error: null,
    started_at: null,
    finished_at: null,
    created_at: "2026-04-28T12:00:00Z",
    events_url: `/trees/${TREE_ID}/hypotheses/compute-jobs/${JOB_ID}/events`,
  };
}

describe("BulkComputePanel", () => {
  it("shows the start button before any job is active", () => {
    renderWithClient(<BulkComputePanel treeId={TREE_ID} />);
    expect(screen.getByRole("button", { name: /Compute all hypotheses/i })).toBeEnabled();
    expect(screen.queryByTestId("bulk-compute-panel")).toBeNull();
  });

  it("starts a job on click and shows progress panel", async () => {
    const startSpy = vi.spyOn(api, "startBulkCompute").mockResolvedValue(makeQueuedJob());
    const user = userEvent.setup();

    renderWithClient(<BulkComputePanel treeId={TREE_ID} />);
    await user.click(screen.getByRole("button", { name: /Compute all hypotheses/i }));

    expect(startSpy).toHaveBeenCalledWith(TREE_ID);
    await waitFor(() => expect(screen.getByTestId("bulk-compute-panel")).toBeInTheDocument());
    // На queued стадии — пока нет первого SSE-фрейма, рендерим
    // statusToStage(queued) → loading_rules.
    expect(screen.getByText("Loading rule registry")).toBeInTheDocument();
  });

  it("renders SSE progress event as iterating_pairs", async () => {
    vi.spyOn(api, "startBulkCompute").mockResolvedValue(makeQueuedJob());
    const user = userEvent.setup();

    renderWithClient(<BulkComputePanel treeId={TREE_ID} />);
    await user.click(screen.getByRole("button", { name: /Compute all hypotheses/i }));
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));

    const es = MockEventSource.instances[0];
    expect(es?.url).toContain(`/trees/${TREE_ID}/hypotheses/compute-jobs/${JOB_ID}/events`);

    act(() => {
      es?.open();
      es?.message({
        stage: "iterating_pairs",
        current: 30,
        total: 100,
        message: "Iterating person pairs (30/100)",
      });
    });

    await waitFor(() => expect(screen.getByText("Iterating person pairs")).toBeInTheDocument());
    expect(screen.getByText("30 / 100")).toBeInTheDocument();
  });

  it("calls onCompleted and shows success banner on succeeded stage", async () => {
    vi.spyOn(api, "startBulkCompute").mockResolvedValue(makeQueuedJob());
    const onCompleted = vi.fn();
    const user = userEvent.setup();

    renderWithClient(<BulkComputePanel treeId={TREE_ID} onCompleted={onCompleted} />);
    await user.click(screen.getByRole("button", { name: /Compute all hypotheses/i }));
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));

    const es = MockEventSource.instances[0];
    act(() => {
      es?.open();
      es?.message({
        stage: "succeeded",
        current: 100,
        total: 100,
        message: "Created 7 hypotheses",
      });
    });

    await waitFor(() => expect(screen.getByTestId("bulk-compute-success")).toBeInTheDocument());
    expect(onCompleted).toHaveBeenCalled();
    // Кнопка cancel заменяется на «Dismiss» на терминальной стадии.
    expect(screen.queryByRole("button", { name: /^Cancel$/i })).toBeNull();
    expect(screen.getByRole("button", { name: /Dismiss/i })).toBeInTheDocument();
  });

  it("invokes cancel API on cancel click", async () => {
    vi.spyOn(api, "startBulkCompute").mockResolvedValue(makeQueuedJob());
    const cancelSpy = vi.spyOn(api, "cancelBulkComputeJob").mockResolvedValue({
      ...makeQueuedJob(),
      status: "running",
      cancel_requested: true,
    });
    const user = userEvent.setup();

    renderWithClient(<BulkComputePanel treeId={TREE_ID} />);
    await user.click(screen.getByRole("button", { name: /Compute all hypotheses/i }));
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));

    const es = MockEventSource.instances[0];
    act(() => {
      es?.open();
      es?.message({
        stage: "iterating_pairs",
        current: 5,
        total: 100,
      });
    });

    await user.click(screen.getByRole("button", { name: /^Cancel$/i }));
    expect(cancelSpy).toHaveBeenCalledWith(JOB_ID);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Cancel requested/i })).toBeDisabled(),
    );
  });

  it("renders error message when start fails", async () => {
    vi.spyOn(api, "startBulkCompute").mockRejectedValue(
      new api.ApiError(500, "Internal server error"),
    );
    const user = userEvent.setup();

    renderWithClient(<BulkComputePanel treeId={TREE_ID} />);
    await user.click(screen.getByRole("button", { name: /Compute all hypotheses/i }));

    await waitFor(() =>
      expect(screen.getByTestId("bulk-compute-error")).toHaveTextContent(
        /500: Internal server error/,
      ),
    );
    // Панель не открывается — мы остаёмся на idle-кнопке.
    expect(screen.queryByTestId("bulk-compute-panel")).toBeNull();
  });
});
