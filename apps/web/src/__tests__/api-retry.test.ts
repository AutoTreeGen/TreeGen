import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchPersons, setFetchImpl, setUnauthorizedHandler } from "@/lib/api";
import {
  AuthError,
  NetworkError,
  ServerError,
  ValidationError,
  classifyHttpError,
  isRetryableError,
} from "@/lib/errors";
import { withRetry } from "@/lib/retry";

function jsonResponse(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

function errorResponse(status: number, detail?: string): Response {
  return new Response(JSON.stringify(detail ? { detail } : {}), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("classifyHttpError", () => {
  it("maps 401 to AuthError", () => {
    expect(classifyHttpError(401, "x")).toBeInstanceOf(AuthError);
  });
  it("maps 403 to AuthError", () => {
    expect(classifyHttpError(403, "x")).toBeInstanceOf(AuthError);
  });
  it("maps 4xx (non auth) to ValidationError", () => {
    expect(classifyHttpError(400, "x")).toBeInstanceOf(ValidationError);
    expect(classifyHttpError(422, "x")).toBeInstanceOf(ValidationError);
  });
  it("maps 5xx to ServerError", () => {
    expect(classifyHttpError(500, "x")).toBeInstanceOf(ServerError);
    expect(classifyHttpError(503, "x")).toBeInstanceOf(ServerError);
  });
});

describe("isRetryableError", () => {
  it("retries NetworkError + ServerError, not Auth/Validation", () => {
    expect(isRetryableError(new NetworkError())).toBe(true);
    expect(isRetryableError(new ServerError(500, "x"))).toBe(true);
    expect(isRetryableError(new AuthError(401, "x"))).toBe(false);
    expect(isRetryableError(new ValidationError(422, "x"))).toBe(false);
    expect(isRetryableError(new Error("plain"))).toBe(false);
  });
});

describe("withRetry", () => {
  it("returns first-attempt result when no error", async () => {
    const fn = vi.fn().mockResolvedValue("ok");
    const out = await withRetry(fn, { sleep: async () => {} });
    expect(out).toBe("ok");
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("retries up to maxAttempts on retryable errors", async () => {
    const fn = vi
      .fn()
      .mockRejectedValueOnce(new NetworkError())
      .mockRejectedValueOnce(new NetworkError())
      .mockResolvedValue("ok");
    const out = await withRetry(fn, { maxAttempts: 3, sleep: async () => {} });
    expect(out).toBe("ok");
    expect(fn).toHaveBeenCalledTimes(3);
  });

  it("does not retry non-retryable errors", async () => {
    const fn = vi.fn().mockRejectedValue(new ValidationError(422, "bad"));
    await expect(withRetry(fn, { sleep: async () => {} })).rejects.toBeInstanceOf(ValidationError);
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("re-throws after exhausting attempts", async () => {
    const fn = vi.fn().mockRejectedValue(new NetworkError("nope"));
    await expect(withRetry(fn, { maxAttempts: 2, sleep: async () => {} })).rejects.toBeInstanceOf(
      NetworkError,
    );
    expect(fn).toHaveBeenCalledTimes(2);
  });
});

describe("api.ts integration with retry + 401 handler", () => {
  let restoreFetch: typeof globalThis.fetch;
  beforeEach(() => {
    restoreFetch = globalThis.fetch;
  });
  afterEach(() => {
    globalThis.fetch = restoreFetch;
    setFetchImpl((...args) => fetch(...args));
    setUnauthorizedHandler(() => {});
  });

  it("retries 5xx and eventually succeeds", async () => {
    const calls: number[] = [];
    setFetchImpl(async () => {
      calls.push(calls.length);
      if (calls.length < 3) return errorResponse(503, "tree thinking");
      return jsonResponse({
        tree_id: "t1",
        total: 0,
        limit: 50,
        offset: 0,
        items: [],
      });
    });
    const result = await fetchPersons("t1");
    expect(result.tree_id).toBe("t1");
    expect(calls.length).toBe(3);
  });

  it("does not retry 422 and throws ValidationError", async () => {
    let count = 0;
    setFetchImpl(async () => {
      count += 1;
      return errorResponse(422, "nope");
    });
    await expect(fetchPersons("bad-tree")).rejects.toBeInstanceOf(ValidationError);
    expect(count).toBe(1);
  });

  it("calls unauthorized handler on 401 and re-throws AuthError", async () => {
    setFetchImpl(async () => errorResponse(401, "no auth"));
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    await expect(fetchPersons("t1")).rejects.toBeInstanceOf(AuthError);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("converts thrown TypeError from fetch into NetworkError", async () => {
    setFetchImpl(() => {
      throw new TypeError("Failed to fetch");
    });
    await expect(fetchPersons("t1")).rejects.toBeInstanceOf(NetworkError);
  });
});
