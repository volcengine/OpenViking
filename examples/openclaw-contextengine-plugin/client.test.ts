import { afterEach, describe, expect, it, vi } from "vitest";

import { createOpenVikingClient } from "./client.js";

describe("OpenViking client", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("normalizes base URL without trailing slash", () => {
    const c = createOpenVikingClient({
      baseUrl: "http://127.0.0.1:1933/",
      timeoutMs: 15000,
      apiKey: "",
    });

    expect(c.baseUrl).toBe("http://127.0.0.1:1933");
  });

  it("calls health endpoint with auth and agent headers", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ status: "ok" }),
    } as Response);

    const c = createOpenVikingClient({
      baseUrl: "http://127.0.0.1:1933/",
      timeoutMs: 15000,
      apiKey: "secret",
      agentId: "agent-1",
    });

    await expect(c.health()).resolves.toBe(true);

    const call = fetchMock.mock.calls[0];
    expect(call?.[0]).toBe("http://127.0.0.1:1933/health");
    const headers = new Headers((call?.[1] as RequestInit | undefined)?.headers);
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get("Authorization")).toBe("Bearer secret");
    expect(headers.get("X-OpenViking-Agent")).toBe("agent-1");
  });

  it("throws on non-ok responses", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ message: "boom" }),
    } as Response);

    const c = createOpenVikingClient({
      baseUrl: "http://127.0.0.1:1933",
      timeoutMs: 15000,
    });

    await expect(c.find("hello")).rejects.toThrow(/OpenViking request failed \(500\) on \/api\/v1\/search\/find/);
  });

  it("aborts request when timeout is exceeded", async () => {
    let aborted = false;
    vi.spyOn(globalThis, "fetch").mockImplementation((_, init) => {
      return new Promise((_, reject) => {
        const signal = (init as RequestInit | undefined)?.signal;
        signal?.addEventListener("abort", () => {
          aborted = true;
          reject(new Error("aborted"));
        });
      });
    });

    const c = createOpenVikingClient({
      baseUrl: "http://127.0.0.1:1933",
      timeoutMs: 1,
    });

    await expect(c.find("slow")).rejects.toThrow(/request timeout after 1ms/);
    expect(aborted).toBe(true);
  });

  it("unwraps OpenViking envelope result for session creation", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        status: "ok",
        result: { session_id: "s-envelope" },
      }),
    } as Response);

    const c = createOpenVikingClient({
      baseUrl: "http://127.0.0.1:1933",
      timeoutMs: 15000,
    });

    await expect(c.createSession()).resolves.toBe("s-envelope");
  });

  it("throws when OpenViking envelope returns non-ok status", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        status: "error",
        error: "upstream unavailable",
      }),
    } as Response);

    const c = createOpenVikingClient({
      baseUrl: "http://127.0.0.1:1933",
      timeoutMs: 15000,
    });

    await expect(c.find("hello")).rejects.toThrow(/upstream unavailable/);
  });
  it("maps commit extracted_count to extractedCount", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ extracted_count: 3 }),
    } as Response);

    const c = createOpenVikingClient({
      baseUrl: "http://127.0.0.1:1933",
      timeoutMs: 15000,
    });

    await expect(c.commitSession("s1")).resolves.toEqual({ extractedCount: 3 });
  });

  it("handles 204 responses without parsing json", async () => {
    const json = vi.fn(async () => ({}));
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      status: 204,
      json,
    } as unknown as Response);

    const c = createOpenVikingClient({
      baseUrl: "http://127.0.0.1:1933",
      timeoutMs: 15000,
    });

    await expect(c.deleteSession("s1")).resolves.toBeUndefined();
    expect(json).not.toHaveBeenCalled();
  });
});
