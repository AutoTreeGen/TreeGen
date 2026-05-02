import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConsentBanner } from "@/components/voice/consent-banner";
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

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ConsentBanner", () => {
  it("shows the consent body and grant button when no consent is set", async () => {
    vi.spyOn(voiceApi, "fetchAudioConsent").mockResolvedValue({
      tree_id: TREE_ID,
      audio_consent_egress_at: null,
      audio_consent_egress_provider: null,
    });

    render(wrap(<ConsentBanner treeId={TREE_ID} canManageConsent={true} />));

    await waitFor(() => {
      expect(screen.getByTestId("consent-grant")).toBeInTheDocument();
    });
    expect(screen.getByText(/OpenAI Whisper/)).toBeInTheDocument();
    expect(screen.queryByTestId("consent-revoke")).toBeNull();
  });

  it("posts grant when the I-consent button is clicked", async () => {
    vi.spyOn(voiceApi, "fetchAudioConsent").mockResolvedValue({
      tree_id: TREE_ID,
      audio_consent_egress_at: null,
      audio_consent_egress_provider: null,
    });
    const grant = vi.spyOn(voiceApi, "grantAudioConsent").mockResolvedValue({
      tree_id: TREE_ID,
      audio_consent_egress_at: "2026-05-02T10:00:00Z",
      audio_consent_egress_provider: "openai",
    });

    render(wrap(<ConsentBanner treeId={TREE_ID} canManageConsent={true} />));

    fireEvent.click(await screen.findByTestId("consent-grant"));

    await waitFor(() => {
      expect(grant).toHaveBeenCalledWith(TREE_ID);
    });
    // After mutation, we render granted-state with timestamp + revoke button.
    await waitFor(() => {
      expect(screen.getByTestId("consent-revoke")).toBeInTheDocument();
    });
  });

  it("shows revoke button and timestamp when consent is already granted", async () => {
    vi.spyOn(voiceApi, "fetchAudioConsent").mockResolvedValue({
      tree_id: TREE_ID,
      audio_consent_egress_at: "2026-05-01T08:30:00Z",
      audio_consent_egress_provider: "openai",
    });

    render(wrap(<ConsentBanner treeId={TREE_ID} canManageConsent={true} />));

    await waitFor(() => {
      expect(screen.getByTestId("consent-revoke")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("consent-grant")).toBeNull();
    // grantedAt template includes "Consent granted on …".
    expect(screen.getByText(/Consent granted on/)).toBeInTheDocument();
  });

  it("hides grant/revoke buttons for non-owner viewers", async () => {
    vi.spyOn(voiceApi, "fetchAudioConsent").mockResolvedValue({
      tree_id: TREE_ID,
      audio_consent_egress_at: null,
      audio_consent_egress_provider: null,
    });

    render(wrap(<ConsentBanner treeId={TREE_ID} canManageConsent={false} />));

    await waitFor(() => {
      expect(screen.getByText(/Only the tree owner/)).toBeInTheDocument();
    });
    expect(screen.queryByTestId("consent-grant")).toBeNull();
    expect(screen.queryByTestId("consent-revoke")).toBeNull();
  });

  it("revokes consent after window.confirm and clears the cached state", async () => {
    vi.spyOn(voiceApi, "fetchAudioConsent").mockResolvedValue({
      tree_id: TREE_ID,
      audio_consent_egress_at: "2026-05-01T08:30:00Z",
      audio_consent_egress_provider: "openai",
    });
    const revoke = vi.spyOn(voiceApi, "revokeAudioConsent").mockResolvedValue({
      tree_id: TREE_ID,
      revoked_at: "2026-05-02T10:30:00Z",
      enqueued_session_ids: ["sess-1", "sess-2"],
    });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(wrap(<ConsentBanner treeId={TREE_ID} canManageConsent={true} />));

    fireEvent.click(await screen.findByTestId("consent-revoke"));

    await waitFor(() => {
      expect(revoke).toHaveBeenCalledWith(TREE_ID);
    });
    expect(confirmSpy).toHaveBeenCalled();
    // Post-revoke: cleared state → grant button visible again.
    await waitFor(() => {
      expect(screen.getByTestId("consent-grant")).toBeInTheDocument();
    });
  });

  it("does not revoke when the user cancels window.confirm", async () => {
    vi.spyOn(voiceApi, "fetchAudioConsent").mockResolvedValue({
      tree_id: TREE_ID,
      audio_consent_egress_at: "2026-05-01T08:30:00Z",
      audio_consent_egress_provider: "openai",
    });
    const revoke = vi.spyOn(voiceApi, "revokeAudioConsent");
    vi.spyOn(window, "confirm").mockReturnValue(false);

    render(wrap(<ConsentBanner treeId={TREE_ID} canManageConsent={true} />));

    fireEvent.click(await screen.findByTestId("consent-revoke"));

    expect(revoke).not.toHaveBeenCalled();
  });
});
