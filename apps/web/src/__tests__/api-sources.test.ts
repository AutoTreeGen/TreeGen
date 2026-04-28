import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchSources } from "@/lib/api";

// fetchSources — единственный публичный fetch, у которого появилась
// новая семантика (Phase 4.7-finalize): объект-параметры вместо
// позиционных, опциональный `q`. Фокус — URL building, не network'ом.

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
});

function lastFetchUrl(): string {
  const fetchMock = global.fetch as unknown as ReturnType<typeof vi.fn>;
  expect(fetchMock).toHaveBeenCalled();
  const lastCall = fetchMock.mock.calls.at(-1);
  if (!lastCall) throw new Error("expected at least one fetch call");
  const [url] = lastCall;
  return String(url);
}

describe("fetchSources", () => {
  it("omits `q` when not provided", async () => {
    await fetchSources("tree-1");
    const url = lastFetchUrl();
    expect(url).toContain("/trees/tree-1/sources");
    expect(url).toContain("limit=50");
    expect(url).toContain("offset=0");
    expect(url).not.toContain("q=");
  });

  it("includes `q` when provided", async () => {
    await fetchSources("tree-1", { q: "Bible" });
    const url = lastFetchUrl();
    expect(url).toContain("q=Bible");
  });

  it("URL-encodes the `q` value (spaces, ampersands)", async () => {
    await fetchSources("tree-1", { q: "Anna Smith & Co" });
    const url = lastFetchUrl();
    // URLSearchParams encodes spaces as `+` and `&` as `%26`.
    expect(url).toContain("q=Anna+Smith+%26+Co");
  });

  it("omits empty-string `q` (debounced state may be '')", async () => {
    // page calls fetchSources({ q: debouncedQ || undefined }) — но если
    // вызывающий передал пустую строку напрямую, она тоже не должна
    // улететь, иначе backend получит `q=` и вернёт 0 совпадений.
    await fetchSources("tree-1", { q: "" });
    expect(lastFetchUrl()).not.toContain("q=");
  });

  it("forwards custom limit/offset", async () => {
    await fetchSources("tree-1", { limit: 100, offset: 200 });
    const url = lastFetchUrl();
    expect(url).toContain("limit=100");
    expect(url).toContain("offset=200");
  });
});
