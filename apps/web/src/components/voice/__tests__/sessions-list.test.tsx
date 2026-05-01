import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SessionsList } from "@/components/voice/sessions-list";
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

function makeSession(
  overrides: Partial<voiceApi.AudioSessionResponse> = {},
): voiceApi.AudioSessionResponse {
  return {
    id: "sess-1",
    tree_id: TREE_ID,
    status: "ready",
    storage_uri: "s3://bucket/sessions/sess-1.webm",
    mime_type: "audio/webm",
    duration_sec: 12,
    size_bytes: 16_384,
    language: "ru",
    transcript_text: "Бабушка родилась в Минске.",
    transcript_provider: "openai-whisper",
    transcript_model_version: "whisper-1",
    transcript_cost_usd: "0.0042",
    error_message: null,
    created_at: "2026-05-02T10:00:00Z",
    updated_at: "2026-05-02T10:00:30Z",
    deleted_at: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SessionsList", () => {
  it("shows the empty state when the API returns no sessions", async () => {
    vi.spyOn(voiceApi, "fetchAudioSessions").mockResolvedValue({
      tree_id: TREE_ID,
      total: 0,
      page: 1,
      per_page: 50,
      items: [],
    });

    render(wrap(<SessionsList treeId={TREE_ID} />));

    await waitFor(() => {
      expect(screen.getByText(/No recordings yet/i)).toBeInTheDocument();
    });
  });

  it("renders a session card with its status badge", async () => {
    vi.spyOn(voiceApi, "fetchAudioSessions").mockResolvedValue({
      tree_id: TREE_ID,
      total: 1,
      page: 1,
      per_page: 50,
      items: [makeSession()],
    });

    render(wrap(<SessionsList treeId={TREE_ID} />));

    await waitFor(() => {
      expect(screen.getByTestId("session-item-sess-1")).toBeInTheDocument();
    });
    expect(screen.getByText("Ready")).toBeInTheDocument();
  });

  it("expands the transcript when 'View transcript' is clicked", async () => {
    vi.spyOn(voiceApi, "fetchAudioSessions").mockResolvedValue({
      tree_id: TREE_ID,
      total: 1,
      page: 1,
      per_page: 50,
      items: [makeSession()],
    });

    render(wrap(<SessionsList treeId={TREE_ID} />));

    fireEvent.click(await screen.findByTestId("session-toggle-sess-1"));

    await waitFor(() => {
      expect(screen.getByTestId("transcript-viewer")).toBeInTheDocument();
    });
    expect(screen.getByText(/Бабушка родилась в Минске/)).toBeInTheDocument();
    // Cost rendered with 4-decimal precision (Whisper cost).
    expect(screen.getByText(/Cost: \$0.0042/)).toBeInTheDocument();
  });

  it("shows the transcribing badge while a session is in flight", async () => {
    vi.spyOn(voiceApi, "fetchAudioSessions").mockResolvedValue({
      tree_id: TREE_ID,
      total: 1,
      page: 1,
      per_page: 50,
      items: [
        makeSession({
          id: "sess-2",
          status: "transcribing",
          transcript_text: null,
        }),
      ],
    });

    render(wrap(<SessionsList treeId={TREE_ID} />));
    await waitFor(() => {
      expect(screen.getByText("Transcribing…")).toBeInTheDocument();
    });
  });

  it("calls softDeleteAudioSession after window.confirm and reloads the list", async () => {
    const fetchSpy = vi.spyOn(voiceApi, "fetchAudioSessions").mockResolvedValue({
      tree_id: TREE_ID,
      total: 1,
      page: 1,
      per_page: 50,
      items: [makeSession()],
    });
    const remove = vi
      .spyOn(voiceApi, "softDeleteAudioSession")
      .mockResolvedValue(makeSession({ deleted_at: "2026-05-02T11:00:00Z" }));
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(wrap(<SessionsList treeId={TREE_ID} />));

    fireEvent.click(await screen.findByTestId("session-delete-sess-1"));

    await waitFor(() => {
      expect(remove).toHaveBeenCalledWith("sess-1");
    });
    // After delete, the list invalidates and refetches.
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledTimes(2);
    });
  });
});
