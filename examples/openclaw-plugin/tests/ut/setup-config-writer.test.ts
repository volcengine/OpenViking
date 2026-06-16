import { describe, expect, it } from "vitest";

import {
  activateContextEngineSlot,
  ensureInstallRecord,
  getExistingPluginConfig,
  isContextEngineSlotActive,
  writeOpenVikingConfig,
  type SetupIO,
} from "../../services/setup/config-writer.js";

function createMemoryIO(initialConfig: Record<string, unknown> = {}): SetupIO & {
  config: Record<string, unknown>;
  backups: string[];
} {
  return {
    config: structuredClone(initialConfig),
    backups: [],
    readConfig() {
      return structuredClone(this.config);
    },
    writeConfig(_configPath: string, config: Record<string, unknown>) {
      this.config = structuredClone(config);
    },
    backupConfig(configPath: string) {
      const backupPath = `${configPath}.bak`;
      this.backups.push(backupPath);
      return backupPath;
    },
  };
}

describe("setup config writer service", () => {
  it("writes OpenViking plugin config while preserving entry metadata and cleaning stale install records", () => {
    const io = createMemoryIO({
      plugins: {
        allow: ["other"],
        installs: { openviking: { path: "/legacy" }, other: { path: "/other" } },
        entries: {
          openviking: { enabled: true, config: { mode: "local", port: 1933 } },
        },
      },
    });

    writeOpenVikingConfig("/tmp/openclaw.json", {
      mode: "remote",
      baseUrl: "http://127.0.0.1:1933",
    }, io);

    expect(io.backups).toEqual(["/tmp/openclaw.json.bak"]);
    expect(io.config).toMatchObject({
      plugins: {
        allow: ["other", "openviking"],
        installs: { other: { path: "/other" } },
        entries: {
          openviking: {
            enabled: true,
            config: {
              mode: "remote",
              baseUrl: "http://127.0.0.1:1933",
            },
          },
        },
      },
    });
  });

  it("protects an occupied contextEngine slot unless forced", () => {
    const io = createMemoryIO({ plugins: { slots: { contextEngine: "other-engine" } } });

    expect(activateContextEngineSlot("/tmp/openclaw.json", false, io)).toEqual({
      activated: false,
      previousOwner: "other-engine",
      replaced: false,
    });
    expect(isContextEngineSlotActive("/tmp/openclaw.json", io)).toBe(false);

    expect(activateContextEngineSlot("/tmp/openclaw.json", true, io)).toEqual({
      activated: true,
      previousOwner: "other-engine",
      replaced: true,
    });
    expect(isContextEngineSlotActive("/tmp/openclaw.json", io)).toBe(true);
  });

  it("detects existing plugin config only when a mode is present", () => {
    expect(getExistingPluginConfig({ plugins: { entries: { openviking: { config: { mode: "remote" } } } } })).toEqual({
      mode: "remote",
    });
    expect(getExistingPluginConfig({ plugins: { entries: { openviking: { config: { baseUrl: "http://x" } } } } })).toBeNull();
  });

  it("ensures openviking is allowed without duplicating allow entries", () => {
    const plugins: Record<string, unknown> = {
      allow: ["openviking"],
      installs: { openviking: { path: "/legacy" } },
    };

    ensureInstallRecord(plugins);

    expect(plugins.allow).toEqual(["openviking"]);
    expect(plugins.installs).toEqual({});
  });
});
