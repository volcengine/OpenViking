import { beforeEach, describe, expect, it, vi } from "vitest";

import contextEnginePlugin from "../index.js";
import { localClientCache, localClientPendingPromises } from "../client.js";

// Regression for #1214 COLLABORATOR review by Mijamind719:
// Second register() must preserve the first registration's pending
// startup entry; otherwise the first registration's captured
// clientPromise is orphaned and getClient() hangs.
describe("plugin re-register preserves pending startup entries (#1214)", () => {
  beforeEach(() => {
    localClientCache.clear();
    localClientPendingPromises.clear();
  });

  const makeStubApi = (pluginConfig: Record<string, unknown>) => ({
    pluginConfig,
    logger: {
      info: vi.fn(),
      warn: vi.fn(),
      error: vi.fn(),
      debug: vi.fn(),
    },
    registerTool: vi.fn(),
    registerService: vi.fn(),
    registerContextEngine: vi.fn(),
    on: vi.fn(),
  });

  it("keeps the same pending-startup entry after a second register() with the same cacheKey", () => {
    const cfg = {
      mode: "local" as const,
      baseUrl: "http://127.0.0.1:39997",
      configPath: "/tmp/openviking-test-config-1214",
      apiKey: "",
      agentId: "default",
      logFindRequests: false,
      timeoutMs: 5000,
    };

    contextEnginePlugin.register(makeStubApi(cfg) as never);

    expect(localClientPendingPromises.size).toBe(1);
    const firstEntry = [...localClientPendingPromises.values()][0];

    contextEnginePlugin.register(makeStubApi(cfg) as never);

    // Entry identity must be preserved. The first registration's
    // `clientPromise` closure was set to `firstEntry.promise` via the
    // existingPending branch; if a second register() replaced the map
    // slot with a new entry, service startup would later resolve the
    // replacement and the first registration's promise would hang.
    expect(localClientPendingPromises.size).toBe(1);
    const secondEntry = [...localClientPendingPromises.values()][0];
    expect(secondEntry).toBe(firstEntry);

    // Behavioral sanity: resolving the preserved entry lets any closure
    // that captured it settle. Since the second registration reuses the
    // same promise, both registrations' getClient() would observe the
    // same resolved client.
    const fakeClient = { __stub: true };
    firstEntry!.resolve(fakeClient as never);
    return expect(firstEntry!.promise).resolves.toBe(fakeClient);
  });

  it("creates a fresh pending entry on re-register with a different cacheKey (#1210 reload)", () => {
    const cfgA = {
      mode: "local" as const,
      baseUrl: "http://127.0.0.1:39998",
      configPath: "/tmp/openviking-test-config-1214-a",
      apiKey: "",
      agentId: "agent-a",
      logFindRequests: false,
      timeoutMs: 5000,
    };
    const cfgB = { ...cfgA, agentId: "agent-b" };

    contextEnginePlugin.register(makeStubApi(cfgA) as never);
    expect(localClientPendingPromises.size).toBe(1);

    contextEnginePlugin.register(makeStubApi(cfgB) as never);
    // Distinct cacheKeys → each registration owns its own entry; no
    // cross-contamination and no accidental reuse of the other's promise.
    expect(localClientPendingPromises.size).toBe(2);
  });
});
