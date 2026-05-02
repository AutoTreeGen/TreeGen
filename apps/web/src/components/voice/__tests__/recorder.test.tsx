import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Recorder } from "@/components/voice/recorder";
import * as voiceApi from "@/lib/voice-api";
import enMessages from "../../../../messages/en.json";

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

const TREE_ID = "tree-1";

/**
 * Минимальный MediaRecorder mock, достаточный для recorder.tsx:
 *  - конструктор сохраняет options.mimeType
 *  - .start() ставит state="recording"
 *  - .stop() триггерит ondataavailable + onstop с накопленным blob'ом
 *  - .isTypeSupported() static — true для webm/opus
 */
type RecorderEvent = { data: Blob };

class MockMediaRecorder {
  static isTypeSupported(_type: string): boolean {
    return true;
  }
  state: "inactive" | "recording" | "paused" = "inactive";
  ondataavailable: ((ev: RecorderEvent) => void) | null = null;
  onstop: (() => void) | null = null;
  mimeType: string;

  constructor(_stream: MediaStream, options?: { mimeType?: string }) {
    this.mimeType = options?.mimeType ?? "audio/webm";
  }

  start(): void {
    this.state = "recording";
  }

  stop(): void {
    this.state = "inactive";
    // Эмулируем поток данных — один chunk с непустым телом.
    const blob = new Blob(["fake-audio-bytes"], { type: this.mimeType });
    this.ondataavailable?.({ data: blob });
    this.onstop?.();
  }
}

function installMediaMocks() {
  // jsdom не реализует MediaRecorder и mediaDevices — ставим shim'ы.
  Object.defineProperty(globalThis, "MediaRecorder", {
    configurable: true,
    writable: true,
    value: MockMediaRecorder,
  });
  Object.defineProperty(globalThis.navigator, "mediaDevices", {
    configurable: true,
    value: {
      getUserMedia: vi.fn().mockResolvedValue({
        getTracks: () => [{ stop: () => {} }],
      }),
    },
  });
}

beforeEach(() => {
  installMediaMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Recorder", () => {
  it("disables the start button when consent is not granted", () => {
    render(wrap(<Recorder treeId={TREE_ID} consentGranted={false} />));
    const start = screen.getByTestId("recorder-start") as HTMLButtonElement;
    expect(start.disabled).toBe(true);
    expect(screen.getByText(/Grant consent above/)).toBeInTheDocument();
  });

  it("starts recording, stops after >= min duration, and uploads the blob", async () => {
    // Подменяем Date.now() напрямую — fake-timers ломают react-query'шные
    // microtask'и (mutation тогда не успевает запуститься в pending → settled).
    let now = 1_700_000_000_000;
    vi.spyOn(Date, "now").mockImplementation(() => now);

    const upload = vi.spyOn(voiceApi, "uploadAudioSession").mockResolvedValue({
      id: "sess-1",
      tree_id: TREE_ID,
      status: "uploaded",
      storage_uri: "s3://bucket/sessions/sess-1.webm",
      mime_type: "audio/webm",
      duration_sec: null,
      size_bytes: 16,
      language: null,
      transcript_text: null,
      transcript_provider: null,
      transcript_model_version: null,
      transcript_cost_usd: null,
      error_message: null,
      created_at: "2026-05-02T10:00:00Z",
      updated_at: "2026-05-02T10:00:00Z",
      deleted_at: null,
    });
    const onUploaded = vi.fn();

    render(wrap(<Recorder treeId={TREE_ID} consentGranted={true} onUploaded={onUploaded} />));

    fireEvent.click(screen.getByTestId("recorder-start"));

    await waitFor(() => {
      expect(screen.getByTestId("recorder-stop")).toBeInTheDocument();
    });

    // Прокручиваем «10 секунд» вперёд — выше MIN_DURATION_SEC=5.
    now += 10_000;

    fireEvent.click(screen.getByTestId("recorder-stop"));

    await waitFor(() => {
      expect(upload).toHaveBeenCalledTimes(1);
    });
    const callArgs = upload.mock.calls[0];
    expect(callArgs?.[0]).toBe(TREE_ID);
    expect(callArgs?.[1]).toBeInstanceOf(Blob);
    await waitFor(() => {
      expect(onUploaded).toHaveBeenCalled();
    });
  });

  it("shows an error and skips upload when stop fires before min duration", async () => {
    let now = 1_700_000_000_000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    const upload = vi.spyOn(voiceApi, "uploadAudioSession");

    render(wrap(<Recorder treeId={TREE_ID} consentGranted={true} />));

    fireEvent.click(screen.getByTestId("recorder-start"));

    await waitFor(() => {
      expect(screen.getByTestId("recorder-stop")).toBeInTheDocument();
    });

    // Только 1 секунда — ниже порога.
    now += 1_000;

    fireEvent.click(screen.getByTestId("recorder-stop"));

    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toMatch(/Minimum 5 seconds/);
    });
    expect(upload).not.toHaveBeenCalled();
  });

  it("shows a microphone-permission error when getUserMedia rejects", async () => {
    Object.defineProperty(globalThis.navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: vi.fn().mockRejectedValue(new Error("denied")) },
    });

    render(wrap(<Recorder treeId={TREE_ID} consentGranted={true} />));
    fireEvent.click(screen.getByTestId("recorder-start"));

    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toMatch(/microphone/i);
    });
  });
});
