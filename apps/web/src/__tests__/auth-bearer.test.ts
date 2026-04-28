import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchSources, setAuthTokenProvider } from "@/lib/api";

/**
 * Phase 4.10: фронтенд-обёртка ``setAuthTokenProvider`` должна заставить
 * каждый api-вызов прикреплять ``Authorization: Bearer <token>`` если
 * provider возвращает токен. Проверяем contract без полноценного
 * Clerk-stack'а (он подменяется на provider-stub).
 */

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ tree_id: "t", total: 0, limit: 50, offset: 0, items: [] }),
    } as Response),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  setAuthTokenProvider(null);
});

function lastFetchInit(): RequestInit | undefined {
  const fetchMock = global.fetch as unknown as ReturnType<typeof vi.fn>;
  const lastCall = fetchMock.mock.calls.at(-1);
  if (!lastCall) throw new Error("expected at least one fetch call");
  return lastCall[1] as RequestInit | undefined;
}

describe("api Bearer attachment", () => {
  it("does not include Authorization header when no provider is set", async () => {
    setAuthTokenProvider(null);
    await fetchSources("tree-id");
    const init = lastFetchInit();
    const headers = (init?.headers ?? {}) as Record<string, string>;
    expect(headers.Authorization).toBeUndefined();
  });

  it("includes Bearer token when provider returns a string", async () => {
    setAuthTokenProvider(async () => "mock-jwt-abc");
    await fetchSources("tree-id");
    const init = lastFetchInit();
    const headers = (init?.headers ?? {}) as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer mock-jwt-abc");
  });

  it("does not include header when provider returns null", async () => {
    setAuthTokenProvider(async () => null);
    await fetchSources("tree-id");
    const init = lastFetchInit();
    const headers = (init?.headers ?? {}) as Record<string, string>;
    expect(headers.Authorization).toBeUndefined();
  });
});
