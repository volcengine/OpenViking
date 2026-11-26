import { describe, expect, it, vi } from "vitest";

import { createOpenVikingSetupService, type SetupIO } from "../../services/setup/setup-flow.js";

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
  it.each([["short", "****"], ["sk-root-secret", "sk-r...cret"]])("rejects a root key without tenant identity and masks it", async (apiKey, maskedKey) => {
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
      apiKey,
    });

    expect(result).toMatchObject({
      success: false,
      action: "error",
      config: { mode: "remote", baseUrl: "http://127.0.0.1:1933", apiKey: maskedKey },
      keyProbe: { keyType: "root_key" },
      slot: { activated: false, replaced: false },
    });
    expect(result.error).toContain("Missing: --account-id, --user-id");
    expect(io.config).toEqual({});
  });

  it("interactive remote setup saves the configuration and replaces an occupied context engine only when forced", async () => {
    const existing = {
      mode: "local",
      port: 1933,
      configPath: "/tmp/openviking",
      customFlag: "keep-me",
      apiKey: "old-key",
      peer_role: "assistant",
      peer_prefix: "old-prefix",
      accountId: "old-account",
      userId: "old-user",
    };
    const io = createMemoryIO({
      plugins: {
        entries: { openviking: { config: existing } },
        slots: { contextEngine: "other-engine" },
      },
    });
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

    const configPath = "/tmp/openclaw.json";
    const params = {
      existing,
      baseUrl: "http://remote:1933",
      apiKey: "",
      peerRole: undefined,
      peerPrefix: "",
      accountId: "",
      userId: "new-user",
    };
    const expectedConfig = {
      mode: "remote",
      baseUrl: "http://remote:1933",
      customFlag: "keep-me",
      userId: "new-user",
    };
    const regular = await service.saveInteractiveRemoteConfig(configPath, params);
    expect(regular).toEqual({
      config: expectedConfig,
      slot: { activated: false, previousOwner: "other-engine", replaced: false },
    });
    expect(io.config).toHaveProperty("plugins.slots.contextEngine", "other-engine");
    expect(io.config).toHaveProperty("plugins.entries.openviking.config", expectedConfig);

    const forced = await service.saveInteractiveRemoteConfig(configPath, {
      ...params,
      existing: regular.config,
      forceSlot: true,
    });
    expect(forced.slot).toEqual({ activated: true, previousOwner: "other-engine", replaced: true });
    expect(io.config).toHaveProperty("plugins.slots.contextEngine", "openviking");
    expect(io.config).toHaveProperty("plugins.entries.openviking.config", expectedConfig);
  });

  it("existing remote config flow checks health, masks secrets, and activates the slot via the service seam", async () => {
    const io = createMemoryIO({
      plugins: {
        entries: {
          openviking: {
            config: {
              mode: "remote",
              baseUrl: "http://127.0.0.1:1933",
              apiKey: "sk-existing-secret",
              peer_role: "assistant",
              peer_prefix: "main",
            },
          },
        },
      },
    });
    const health = {
      ok: true,
      version: "2026.6.1",
      error: "",
      compatibility: "compatible" as const,
      pluginVersion: "2026.6.5",
      compatRange: "any",
    };
    const checkServiceHealth = vi.fn().mockResolvedValue(health);
    const service = createOpenVikingSetupService({
      io,
      checkServiceHealth,
      probeApiKeyType: async () => {
        throw new Error("existing config flow should not probe API keys");
      },
    });

    const existing = {
      mode: "remote",
      baseUrl: "http://127.0.0.1:1933",
      apiKey: "sk-existing-secret",
      peer_role: "assistant",
      peer_prefix: "main",
    };
    const result = await service.useExistingRemoteConfig("/tmp/openclaw.json", existing);

    expect(checkServiceHealth).toHaveBeenCalledWith("http://127.0.0.1:1933", "sk-existing-secret");
    expect(result).toMatchObject({
      success: true,
      action: "existing",
      config: {
        mode: "remote",
        baseUrl: "http://127.0.0.1:1933",
        apiKey: "sk-e...cret",
        peer_role: "assistant",
        peer_prefix: "main",
      },
      health,
      slot: { activated: true, replaced: false },
    });
    expect(io.config).toMatchObject({
      plugins: { slots: { contextEngine: "openviking" } },
    });
  });
});
