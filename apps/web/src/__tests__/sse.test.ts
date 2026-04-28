import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { type ImportJobProgressEvent, isTerminalStage, useEventSource } from "@/lib/sse";

// ---------------------------------------------------------------------------
// MockEventSource — небольшой стенд под native EventSource. Не пытается быть
// полноценным polyfill'ом: ровно столько, сколько нужно useEventSource'у.
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

  // Тестовые хуки для имитации событий со стороны "сервера".
  open(): void {
    this.readyState = MockEventSource.OPEN;
    this.onopen?.(new Event("open"));
  }

  message(payload: unknown): void {
    const ev = new MessageEvent("message", { data: JSON.stringify(payload) });
    this.onmessage?.(ev);
  }

  rawMessage(data: string): void {
    const ev = new MessageEvent("message", { data });
    this.onmessage?.(ev);
  }

  error(): void {
    this.readyState = MockEventSource.CLOSED;
    this.onerror?.(new Event("error"));
  }

  close(): void {
    this.readyState = MockEventSource.CLOSED;
  }
}

const factory = (url: string) => new MockEventSource(url) as unknown as EventSource;

beforeEach(() => {
  MockEventSource.instances = [];
  // useEventSource внутри проверяет ``EventSource.CLOSED`` как enum-константу.
  // В jsdom-окружении ``EventSource`` отсутствует — подставляем заглушку.
  (globalThis as unknown as { EventSource: typeof MockEventSource }).EventSource = MockEventSource;
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

function makeEvent(overrides: Partial<ImportJobProgressEvent> = {}): ImportJobProgressEvent {
  return {
    job_id: "job-1",
    stage: "PARSING",
    done: 0,
    total: 0,
    emitted_at: "2026-04-28T12:00:00Z",
    ...overrides,
  };
}

describe("useEventSource", () => {
  it("opens a connection on mount and exposes connected state", () => {
    const { result } = renderHook(() =>
      useEventSource<ImportJobProgressEvent>("/imports/x/events", {
        eventSourceFactory: factory,
      }),
    );

    expect(MockEventSource.instances).toHaveLength(1);
    const es = MockEventSource.instances[0];
    expect(es).toBeDefined();
    expect(es?.url).toBe("/imports/x/events");

    act(() => {
      es?.open();
    });

    expect(result.current.isConnected).toBe(true);
    expect(result.current.error).toBeNull();
  });

  it("does not open a connection when url is null", () => {
    renderHook(() => useEventSource<ImportJobProgressEvent>(null, { eventSourceFactory: factory }));

    expect(MockEventSource.instances).toHaveLength(0);
  });

  it("parses JSON events and exposes them via data", () => {
    const { result } = renderHook(() =>
      useEventSource<ImportJobProgressEvent>("/imports/x/events", {
        eventSourceFactory: factory,
      }),
    );

    const es = MockEventSource.instances[0];

    act(() => {
      es?.open();
      es?.message(makeEvent({ stage: "ENTITIES", done: 100, total: 1000 }));
    });

    expect(result.current.data).toEqual(
      expect.objectContaining({ stage: "ENTITIES", done: 100, total: 1000 }),
    );
  });

  it("retries with backoff on disconnect (then succeeds)", async () => {
    const { result } = renderHook(() =>
      useEventSource<ImportJobProgressEvent>("/imports/x/events", {
        eventSourceFactory: factory,
      }),
    );

    const first = MockEventSource.instances[0];
    expect(first).toBeDefined();

    act(() => {
      first?.open();
      first?.error();
    });

    expect(result.current.isConnected).toBe(false);
    expect(result.current.retries).toBe(1);

    // Backoff = 500ms на первой попытке.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });

    expect(MockEventSource.instances).toHaveLength(2);
    const second = MockEventSource.instances[1];
    act(() => {
      second?.open();
    });
    expect(result.current.isConnected).toBe(true);
    expect(result.current.retries).toBe(0);
  });

  it("gives up after MAX_RETRIES disconnects", async () => {
    const { result } = renderHook(() =>
      useEventSource<ImportJobProgressEvent>("/imports/x/events", {
        eventSourceFactory: factory,
      }),
    );

    // Каждый цикл: error → ждём backoff → создаётся новый MockEventSource.
    // После MAX_RETRIES=5 неудач хук перестаёт переподключаться и пишет
    // финальный setError; новых instances больше не создаётся.
    for (let i = 0; i < 6; i += 1) {
      const es = MockEventSource.instances[i];
      expect(es).toBeDefined();
      await act(async () => {
        es?.error();
        await vi.advanceTimersByTimeAsync(10_000);
      });
    }

    expect(result.current.error).not.toBeNull();
    expect(result.current.error?.message).toMatch(/SSE connection failed/i);
    // На шестой попытке retry уже не планировался — instances не растут.
    expect(MockEventSource.instances).toHaveLength(6);
  });

  it("closes the connection on a terminal stage event", () => {
    const { result } = renderHook(() =>
      useEventSource<ImportJobProgressEvent>("/imports/x/events", {
        eventSourceFactory: factory,
      }),
    );
    const es = MockEventSource.instances[0];

    act(() => {
      es?.open();
      es?.message(makeEvent({ stage: "SUCCEEDED", done: 1000, total: 1000 }));
    });

    expect(result.current.data?.stage).toBe("SUCCEEDED");
    expect(result.current.isConnected).toBe(false);
    expect(es?.readyState).toBe(MockEventSource.CLOSED);
  });

  it("close() lets the consumer terminate the connection manually", () => {
    const { result } = renderHook(() =>
      useEventSource<ImportJobProgressEvent>("/imports/x/events", {
        eventSourceFactory: factory,
      }),
    );
    const es = MockEventSource.instances[0];

    act(() => {
      es?.open();
      result.current.close();
    });

    expect(es?.readyState).toBe(MockEventSource.CLOSED);
    expect(result.current.isConnected).toBe(false);
  });

  it("reports a parse error without tearing down the connection", () => {
    const { result } = renderHook(() =>
      useEventSource<ImportJobProgressEvent>("/imports/x/events", {
        eventSourceFactory: factory,
      }),
    );
    const es = MockEventSource.instances[0];

    act(() => {
      es?.open();
      es?.rawMessage("not json {");
    });

    expect(result.current.error).not.toBeNull();
    expect(result.current.isConnected).toBe(true);
  });
});

describe("isTerminalStage", () => {
  it("identifies SUCCEEDED / FAILED / CANCELED as terminal", () => {
    expect(isTerminalStage("SUCCEEDED")).toBe(true);
    expect(isTerminalStage("FAILED")).toBe(true);
    expect(isTerminalStage("CANCELED")).toBe(true);
  });

  it("identifies in-flight stages as non-terminal", () => {
    expect(isTerminalStage("QUEUED")).toBe(false);
    expect(isTerminalStage("PARSING")).toBe(false);
    expect(isTerminalStage("ENTITIES")).toBe(false);
  });
});
