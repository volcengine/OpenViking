import { describe, expect, it } from "vitest";

import { createOpenVikingSetupService, maskKey, type SetupIO } from "../../services/setup/setup-flow.js";

function createMemoryIO(initialConfig: Record<string, unknown> = {}): SetupIO & { config: Record<string, unknown> } {
  return {
    config: structuredClone(initialConfig),
    readConfig() {
      return structuredClone(this.config);
    },
    writeConfig(_configPath: string, config: Record<string, unknown>) {
      this.config = structuredClone(config);
    },
    backupConfig() {
      return null;
    },
  };
}

describe("setup flow service", () => {
  it("masks API keys without exposing full secrets", () => {
    expect(maskKey("short")).toBe("****");
    expect(maskKey("sk-root-secret")).toBe("sk-r...cret");
  });

  it("refuses root keys without tenant identity before writing config", async () => {
    const io = createMemoryIO();
    const service = createOpenVikingSetupService({
      io,
      checkServiceHealth: async () => ({
        ok: true,
        version: "2026.6.1",
        error: "",
        compatibility: "compatible",
        pluginVersion: "2026.6.5",
        compatRange: "any",
      }),
      probeApiKeyType: async () => ({
        keyType: "root_key",
        needsAccountId: true,
        needsUserId: true,
        detail: "tenant context required",
      }),
    });

    const result = await service.setupNonInteractive("/tmp/openclaw.json", {
      baseUrl: "http://127.0.0.1:1933",
      apiKey: "sk-root-secret",
    });

    expect(result).toMatchObject({
      success: false,
      action: "error",
      config: { mode: "remote", baseUrl: "http://127.0.0.1:1933", apiKey: "sk-r...cret" },
      slot: { activated: false, replaced: false },
    });
    expect(result.error).toContain("Missing: --account-id, --user-id");
    expect(io.config).toEqual({});
  });

  it("saves interactive remote config through injected setup IO", async () => {
    const io = createMemoryIO();
    const service = createOpenVikingSetupService({
      io,
      checkServiceHealth: async () => ({
        ok: true,
        version: "2026.6.1",
        error: "",
        compatibility: "compatible",
        pluginVersion: "2026.6.5",
        compatRange: "any",
      }),
      probeApiKeyType: async () => ({
        keyType: "user_key",
        needsAccountId: false,
        needsUserId: false,
        detail: "ok",
      }),
    });

    const result = await service.saveInteractiveRemoteConfig("/tmp/openclaw.json", {
      baseUrl: "http://remote:1933",
      apiKey: "sk-user-secret",
      peerRole: "assistant",
      peerPrefix: "main",
    });

    expect(result).toMatchObject({
      config: {
        mode: "remote",
        baseUrl: "http://remote:1933",
        apiKey: "sk-user-secret",
        peer_role: "assistant",
        peer_prefix: "main",
      },
      slot: { activated: true, replaced: false },
    });
    expect(io.config).toMatchObject({
      plugins: {
        entries: { openviking: { config: result.config } },
        slots: { contextEngine: "openviking" },
      },
    });
  });
});
