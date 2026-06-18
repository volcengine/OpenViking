import { describe, expect, it, vi } from "vitest";

import { createSetupNetworkProbes, type SetupNetworkTransport } from "../../services/setup/probe-service.js";

function response(status: number, body: unknown = ""): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() {
      if (typeof body === "string") throw new Error("not json");
      return body;
    },
    async text() {
      return typeof body === "string" ? body : JSON.stringify(body);
    },
  } as Response;
}

describe("setup probe service", () => {
  it("checks health through an injected transport and maps server version compatibility", async () => {
    const transport = vi.fn<SetupNetworkTransport>().mockResolvedValue(
      response(200, { result: { version: "2026.6.1" } }),
    );
    const probes = createSetupNetworkProbes({
      pluginVersion: "2026.6.5",
      compatRange: ">= 2026.6.0",
      checkVersionCompatibility: (version) => version === "2026.6.1" ? "compatible" : "unknown",
      transport,
    });

    const result = await probes.checkServiceHealth("http://127.0.0.1:1933/", "sk-user-secret");

    expect(transport).toHaveBeenCalledWith("http://127.0.0.1:1933/health", {
      headers: { "X-API-Key": "sk-user-secret" },
      signal: expect.any(AbortSignal),
    });
    expect(result).toEqual({
      ok: true,
      version: "2026.6.1",
      error: "",
      compatibility: "compatible",
      pluginVersion: "2026.6.5",
      compatRange: ">= 2026.6.0",
    });
  });

  it("classifies root API keys when tenant context is required", async () => {
    const transport = vi.fn<SetupNetworkTransport>().mockResolvedValue(
      response(400, "missing X-OpenViking-Account and X-OpenViking-User context"),
    );
    const probes = createSetupNetworkProbes({
      pluginVersion: "2026.6.5",
      compatRange: "any",
      checkVersionCompatibility: () => "compatible",
      transport,
    });

    const result = await probes.probeApiKeyType("http://127.0.0.1:1933", "sk-root-secret");

    expect(transport).toHaveBeenCalledWith("http://127.0.0.1:1933/api/v1/sessions?limit=1", {
      headers: { "X-API-Key": "sk-root-secret" },
      signal: expect.any(AbortSignal),
    });
    expect(result).toEqual({
      keyType: "root_key",
      needsAccountId: true,
      needsUserId: true,
      detail: "missing X-OpenViking-Account and X-OpenViking-User context",
    });
  });

  it("does not hit the network when no API key is configured", async () => {
    const transport = vi.fn<SetupNetworkTransport>();
    const probes = createSetupNetworkProbes({
      pluginVersion: "2026.6.5",
      compatRange: "any",
      checkVersionCompatibility: () => "compatible",
      transport,
    });

    await expect(probes.probeApiKeyType("http://127.0.0.1:1933")).resolves.toEqual({
      keyType: "no_key",
      needsAccountId: false,
      needsUserId: false,
      detail: "No API key configured",
    });
    expect(transport).not.toHaveBeenCalled();
  });
});
