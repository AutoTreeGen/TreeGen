import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { TranscriptViewer } from "@/components/voice/transcript-viewer";
import type { AudioSessionResponse } from "@/lib/voice-api";
import enMessages from "../../../../messages/en.json";

function wrap(ui: ReactNode) {
  return (
    <NextIntlClientProvider locale="en" messages={enMessages}>
      {ui}
    </NextIntlClientProvider>
  );
}

const BASE: AudioSessionResponse = {
  id: "sess-1",
  tree_id: "tree-1",
  status: "ready",
  storage_uri: "s3://bucket/sessions/sess-1.webm",
  mime_type: "audio/webm",
  duration_sec: 18,
  size_bytes: 32_768,
  language: "ru",
  transcript_text: "Дед родился в Витебске в 1907 году.",
  transcript_provider: "openai-whisper",
  transcript_model_version: "whisper-1",
  transcript_cost_usd: "0.0050",
  error_message: null,
  created_at: "2026-05-02T10:00:00Z",
  updated_at: "2026-05-02T10:00:30Z",
  deleted_at: null,
};

describe("TranscriptViewer", () => {
  it("renders transcript text plus provider/model/cost metadata when ready", () => {
    render(wrap(<TranscriptViewer session={BASE} />));
    expect(screen.getByText(/Дед родился в Витебске/)).toBeInTheDocument();
    expect(screen.getByTestId("transcript-caveat").textContent).toMatch(/Auto-generated/);
    expect(screen.getByText(/Provider: openai-whisper/)).toBeInTheDocument();
    expect(screen.getByText(/Model: whisper-1/)).toBeInTheDocument();
    expect(screen.getByText(/Cost: \$0.0050/)).toBeInTheDocument();
  });

  it("shows empty-state copy when transcript_text is missing (still transcribing)", () => {
    const transcribing: AudioSessionResponse = {
      ...BASE,
      status: "transcribing",
      transcript_text: null,
      transcript_provider: null,
      transcript_model_version: null,
      transcript_cost_usd: null,
    };
    render(wrap(<TranscriptViewer session={transcribing} />));
    expect(screen.getByText(/No transcript yet/)).toBeInTheDocument();
    expect(screen.queryByTestId("transcript-caveat")).toBeNull();
  });

  it("renders a failure alert with the backend error message when status is failed", () => {
    const failed: AudioSessionResponse = {
      ...BASE,
      status: "failed",
      transcript_text: null,
      transcript_provider: null,
      transcript_cost_usd: null,
      error_message: "stt_unavailable",
    };
    render(wrap(<TranscriptViewer session={failed} />));
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toMatch(/Transcription failed/);
    expect(alert.textContent).toMatch(/stt_unavailable/);
  });
});
