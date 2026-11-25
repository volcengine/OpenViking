import { afterEach, describe, expect, it, vi } from "vitest";

import { createOpenVikingSetupService, maskKey, type SetupIO } from "../../services/setup/setup-flow.js";

afterEach(() => {
  vi.unstubAllEnvs();
});

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

  it("resolves environment-backed connection settings before the status probe", async () => {
    vi.stubEnv("OPENVIKING_STATUS_TEST_URL", "http://remote:1933");
    vi.stubEnv("OPENVIKING_STATUS_TEST_KEY", "resolved-user-key");
    const io = createMemoryIO({
      plugins: {
        entries: {
          openviking: {
            config: {
              mode: "remote",
              baseUrl: "${OPENVIKING_STATUS_TEST_URL}",
              apiKey: "${OPENVIKING_STATUS_TEST_KEY}",
            },
          },
        },
      },
    });
    const checkServiceHealth = vi.fn(async () => ({
      ok: true,
      version: "2026.6.1",
      error: "",
      compatibility: "compatible" as const,
      pluginVersion: "2026.6.5",
      compatRange: "any",
    }));
    const service = createOpenVikingSetupService({
      io,
      checkServiceHealth,
      probeApiKeyType: async () => ({
        keyType: "user_key",
        needsAccountId: false,
        needsUserId: false,
        detail: "ok",
      }),
    });

    const result = await service.getStatus("/tmp/openclaw.json");

    expect(checkServiceHealth).toHaveBeenCalledWith("http://remote:1933", "resolved-user-key");
    expect(result.health?.ok).toBe(true);
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
