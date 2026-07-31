import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  activateContextEngineSlot,
  isContextEngineSlotActive,
  readOpenClawConfig,
  writeOpenVikingConfig,
  type SetupIO,
} from "../../services/setup/config-writer.js";
import { createOpenVikingSetupService } from "../../services/setup/setup-flow.js";

describe("setup IO seam", () => {
  const tempDirs: string[] = [];

  afterEach(() => {
    for (const dir of tempDirs.splice(0)) {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });

  function tempConfigPath(): string {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "openviking-setup-io-"));
    tempDirs.push(dir);
    return path.join(dir, "openclaw.json");
  }

  it("writes OpenViking plugin config while preserving entry metadata and install allow-list behavior", () => {
    const configPath = tempConfigPath();
    fs.writeFileSync(configPath, JSON.stringify({
      plugins: {
        allow: ["other"],
        installs: { openviking: { path: "/legacy" }, other: { path: "/other" } },
        entries: {
          openviking: { enabled: true, config: { mode: "local", port: 1933 } },
        },
      },
    }, null, 2));

    writeOpenVikingConfig(configPath, {
      mode: "remote",
      baseUrl: "http://127.0.0.1:1933",
      recallTargetTypes: ["resource"],
    });

    const config = readOpenClawConfig(configPath);
    expect(config.plugins).toMatchObject({
      allow: ["other", "openviking"],
      installs: { other: { path: "/other" } },
      entries: {
        openviking: {
          enabled: true,
          config: {
            mode: "remote",
            baseUrl: "http://127.0.0.1:1933",
            recallTargetTypes: ["resource"],
          },
        },
      },
    });
  });

  it("activates contextEngine slot without replacing another owner unless forced", () => {
    const configPath = tempConfigPath();
    fs.writeFileSync(configPath, JSON.stringify({
      plugins: { slots: { contextEngine: "other-engine" } },
    }, null, 2));

    expect(activateContextEngineSlot(configPath)).toEqual({
      activated: false,
      previousOwner: "other-engine",
      replaced: false,
    });
    expect(isContextEngineSlotActive(configPath)).toBe(false);

    expect(activateContextEngineSlot(configPath, true)).toEqual({
      activated: true,
      previousOwner: "other-engine",
      replaced: true,
    });
    expect(isContextEngineSlotActive(configPath)).toBe(true);
  });
});

describe("setup service seam", () => {
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

  it("non-interactive setup refuses root keys without tenant identity before writing config", async () => {
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
      keyProbe: { keyType: "root_key" },
      slot: { activated: false, replaced: false },
    });
    expect(result.error).toContain("Missing: --account-id, --user-id");
    expect(io.config).toEqual({});
  });

  it("non-interactive setup can persist offline config and activate an empty contextEngine slot", async () => {
    const io = createMemoryIO();
    const service = createOpenVikingSetupService({
      io,
      checkServiceHealth: async () => ({
        ok: false,
        version: "",
        error: "offline",
        compatibility: "unknown",
        pluginVersion: "2026.6.5",
        compatRange: "any",
      }),
      probeApiKeyType: async () => {
        throw new Error("probe should be skipped when health is not ok");
      },
    });

    const result = await service.setupNonInteractive("/tmp/openclaw.json", {
      baseUrl: "http://127.0.0.1:1933",
      allowOffline: true,
      peerRole: "assistant",
      peerPrefix: "main",
      recallTargetTypes: ["resource"],
    });

    expect(result).toMatchObject({
      success: true,
      action: "configured",
      config: {
        mode: "remote",
        baseUrl: "http://127.0.0.1:1933",
        peer_role: "assistant",
        peer_prefix: "main",
        recallTargetTypes: ["resource"],
      },
      slot: { activated: true, replaced: false },
    });
    expect(io.config).toMatchObject({
      plugins: {
        allow: ["openviking"],
        entries: {
          openviking: {
            config: {
              mode: "remote",
              baseUrl: "http://127.0.0.1:1933",
              peer_role: "assistant",
              peer_prefix: "main",
              recallTargetTypes: ["resource"],
            },
          },
        },
        slots: { contextEngine: "openviking" },
      },
    });
  });

  it("interactive remote config save preserves existing fields, removes local-only fields, and activates slot", async () => {
    const io = createMemoryIO({
      plugins: {
        entries: {
          openviking: {
            enabled: true,
            config: {
              mode: "local",
              port: 1933,
              configPath: "/tmp/openviking",
              customFlag: "keep-me",
              apiKey: "old-key",
              peer_role: "assistant",
              peer_prefix: "old-prefix",
              accountId: "old-account",
              userId: "old-user",
            },
          },
        },
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

    const result = await service.saveInteractiveRemoteConfig("/tmp/openclaw.json", {
      existing: {
        mode: "local",
        port: 1933,
        configPath: "/tmp/openviking",
        customFlag: "keep-me",
        apiKey: "old-key",
        peer_role: "assistant",
        peer_prefix: "old-prefix",
        accountId: "old-account",
        userId: "old-user",
      },
      baseUrl: "http://remote:1933",
      apiKey: "",
      peerRole: undefined,
      peerPrefix: "",
      accountId: "",
      userId: "new-user",
    });

    expect(result).toEqual({
      config: {
        mode: "remote",
        baseUrl: "http://remote:1933",
        customFlag: "keep-me",
        userId: "new-user",
      },
      slot: { activated: true, replaced: false },
    });
    expect(io.config).toMatchObject({
      plugins: {
        entries: {
          openviking: {
            enabled: true,
            config: {
              mode: "remote",
              baseUrl: "http://remote:1933",
              customFlag: "keep-me",
              userId: "new-user",
            },
          },
        },
        slots: { contextEngine: "openviking" },
      },
    });
    const saved = (((io.config.plugins as Record<string, unknown>).entries as Record<string, unknown>).openviking as Record<string, unknown>).config as Record<string, unknown>;
    expect(saved).not.toHaveProperty("port");
    expect(saved).not.toHaveProperty("configPath");
    expect(saved).not.toHaveProperty("apiKey");
    expect(saved).not.toHaveProperty("peer_prefix");
    expect(saved).not.toHaveProperty("accountId");
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
