import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  fetchAudioConsent,
  fetchAudioSessions,
  grantAudioConsent,
  revokeAudioConsent,
  setFetchImpl,
  uploadAudioSession,
} from "@/lib/voice-api";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  setFetchImpl(((..._args: Parameters<typeof fetch>) =>
    Promise.resolve(jsonResponse({}))) as unknown as typeof fetch);
});

afterEach(() => {
  setFetchImpl(((..._args: Parameters<typeof fetch>) =>
    Promise.resolve(jsonResponse({}))) as unknown as typeof fetch);
});

describe("voice-api", () => {
  it("fetchAudioConsent calls GET /trees/{id}/audio-consent", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const fetchMock: typeof fetch = (input, init) => {
      const url = typeof input === "string" ? input : input.toString();
      calls.push({ url, init });
      return Promise.resolve(
        jsonResponse({
          tree_id: "tree-1",
          audio_consent_egress_at: null,
          audio_consent_egress_provider: null,
        }),
      );
    };
    setFetchImpl(fetchMock);

    const result = await fetchAudioConsent("tree-1");
    expect(result.audio_consent_egress_at).toBeNull();
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toMatch(/\/trees\/tree-1\/audio-consent$/);
    expect((calls[0]?.init?.method ?? "GET").toUpperCase()).toBe("GET");
  });

  it("grantAudioConsent posts JSON body with provider", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const fetchMock: typeof fetch = (input, init) => {
      const url = typeof input === "string" ? input : input.toString();
      calls.push({ url, init });
      return Promise.resolve(
        jsonResponse({
          tree_id: "tree-1",
          audio_consent_egress_at: "2026-05-02T10:00:00Z",
          audio_consent_egress_provider: "openai",
        }),
      );
    };
    setFetchImpl(fetchMock);

    await grantAudioConsent("tree-1");

    expect(calls[0]?.init?.method).toBe("POST");
    expect(calls[0]?.init?.body).toBe(JSON.stringify({ provider: "openai" }));
  });

  it("revokeAudioConsent issues DELETE", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const fetchMock: typeof fetch = (input, init) => {
      const url = typeof input === "string" ? input : input.toString();
      calls.push({ url, init });
      return Promise.resolve(
        jsonResponse({
          tree_id: "tree-1",
          revoked_at: "2026-05-02T10:30:00Z",
          enqueued_session_ids: [],
        }),
      );
    };
    setFetchImpl(fetchMock);

    await revokeAudioConsent("tree-1");
    expect(calls[0]?.init?.method).toBe("DELETE");
    expect(calls[0]?.url).toMatch(/\/trees\/tree-1\/audio-consent$/);
  });

  it("fetchAudioSessions encodes pagination params", async () => {
    const calls: string[] = [];
    const fetchMock: typeof fetch = (input) => {
      const url = typeof input === "string" ? input : input.toString();
      calls.push(url);
      return Promise.resolve(
        jsonResponse({ tree_id: "tree-1", total: 0, page: 2, per_page: 25, items: [] }),
      );
    };
    setFetchImpl(fetchMock);

    await fetchAudioSessions("tree-1", { page: 2, perPage: 25 });
    expect(calls[0]).toMatch(/page=2/);
    expect(calls[0]).toMatch(/per_page=25/);
  });

  it("uploadAudioSession posts multipart/form-data with audio + language hint", async () => {
    let captured: { input: RequestInfo | URL; init?: RequestInit } | null = null;
    const fetchMock: typeof fetch = (input, init) => {
      captured = { input, init };
      return Promise.resolve(
        jsonResponse({
          id: "sess-1",
          tree_id: "tree-1",
          status: "uploaded",
          storage_uri: "s3://bucket/sessions/sess-1.webm",
          mime_type: "audio/webm",
          duration_sec: null,
          size_bytes: 16,
          language: "ru",
          transcript_text: null,
          transcript_provider: null,
          transcript_model_version: null,
          transcript_cost_usd: null,
          error_message: null,
          created_at: "2026-05-02T10:00:00Z",
          updated_at: "2026-05-02T10:00:00Z",
          deleted_at: null,
        }),
      );
    };
    setFetchImpl(fetchMock);

    const blob = new Blob(["fake-audio"], { type: "audio/webm" });
    await uploadAudioSession("tree-1", blob, { languageHint: "ru" });

    expect(captured).not.toBeNull();
    const init = (captured as unknown as { init: RequestInit }).init;
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    const fd = init.body as FormData;
    expect(fd.get("language_hint")).toBe("ru");
    expect(fd.get("audio")).toBeInstanceOf(Blob);
  });

  it("propagates 403 consent_required as a typed ApiError", async () => {
    const fetchMock: typeof fetch = () =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            detail: { error_code: "consent_required", message: "Voice egress consent…" },
          }),
          { status: 403, headers: { "Content-Type": "application/json" } },
        ),
      );
    setFetchImpl(fetchMock);

    const blob = new Blob(["fake-audio"], { type: "audio/webm" });
    await expect(uploadAudioSession("tree-1", blob)).rejects.toThrow(/Voice egress consent/);
  });
});
