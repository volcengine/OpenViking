import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  OPENVIKING_260610_FEATURE,
  OPENVIKING_FEATURE_GATES_RPC,
  createOpenVikingFeatureGateService,
  isPluginVersionAtLeast,
  registerOpenVikingFeatureGatesMethod,
} from "../../plugin/openviking-feature-gates.js";

const tempDirs: string[] = [];

function writeFeatureGatesConfig(content: unknown): string {
  const dir = mkdtempSync(join(tmpdir(), "openviking-feature-gates-"));
  tempDirs.push(dir);
  const configPath = join(dir, "feature-gates.json");
  writeFileSync(configPath, JSON.stringify(content), "utf8");
  return configPath;
}

describe("openviking feature gates", () => {
  afterEach(() => {
    for (const dir of tempDirs.splice(0)) {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("compares date-style plugin versions with beta and revision ordering", () => {
    expect(isPluginVersionAtLeast("2026.6.10", "2026.6.10")).toBe(true);
    expect(isPluginVersionAtLeast("2026.6.10-1", "2026.6.10")).toBe(true);
    expect(isPluginVersionAtLeast("2026.6.10", "2026.6.10-beta.1")).toBe(true);
    expect(isPluginVersionAtLeast("2026.6.10-beta.1", "2026.6.10")).toBe(false);
    expect(isPluginVersionAtLeast("2026.6.9", "2026.6.10")).toBe(false);
    expect(isPluginVersionAtLeast("not-a-version", "2026.6.10")).toBe(false);
  });

  it("returns the 2026.6.10 gate when version and edition match", async () => {
    const configPath = writeFeatureGatesConfig({
      features: {
        [OPENVIKING_260610_FEATURE]: {
          enable: true,
          minPluginVersion: "2026.6.10",
          minOpenclawVersion: "2026.4.8",
          editions: ["arkclaw_enterprise", "arkclaw"],
        },
      },
    });
    const service = createOpenVikingFeatureGateService({
      configPath,
      getPluginVersion: () => "2026.6.10",
      getOpenClawVersion: async () => "2026.4.8",
      getOpenClawVersionSync: () => "2026.4.8",
    });

    await expect(service.getEnabledFeatureGates("arkclaw")).resolves.toEqual([
      OPENVIKING_260610_FEATURE,
    ]);
  });

  it("filters gates by plugin version, OpenClaw version and edition", async () => {
    const configPath = writeFeatureGatesConfig({
      features: {
        [OPENVIKING_260610_FEATURE]: {
          enable: true,
          minPluginVersion: "2026.6.10",
          minOpenclawVersion: "2026.4.8",
          editions: ["arkclaw_enterprise", "arkclaw"],
        },
      },
    });

    await expect(
      createOpenVikingFeatureGateService({
        configPath,
        getPluginVersion: () => "2026.6.9",
        getOpenClawVersion: async () => "2026.4.8",
        getOpenClawVersionSync: () => "2026.4.8",
      }).getEnabledFeatureGates("arkclaw"),
    ).resolves.toEqual([]);

    await expect(
      createOpenVikingFeatureGateService({
        configPath,
        getPluginVersion: () => "2026.6.10",
        getOpenClawVersion: async () => "2026.4.7",
        getOpenClawVersionSync: () => "2026.4.7",
      }).getEnabledFeatureGates("arkclaw"),
    ).resolves.toEqual([]);

    await expect(
      createOpenVikingFeatureGateService({
        configPath,
        getPluginVersion: () => "2026.6.10",
        getOpenClawVersion: async () => "2026.4.8",
        getOpenClawVersionSync: () => "2026.4.8",
      }).getEnabledFeatureGates("other"),
    ).resolves.toEqual([]);
  });

  it("prefers RPC edition over BUSINESS_CARRIER and falls back to carrier list", async () => {
    const previousCarrier = process.env.BUSINESS_CARRIER;
    const configPath = writeFeatureGatesConfig({
      features: {
        [OPENVIKING_260610_FEATURE]: {
          enable: true,
          minPluginVersion: "2026.6.10",
          minOpenclawVersion: "2026.4.8",
          editions: ["arkclaw_enterprise", "arkclaw"],
        },
      },
    });
    const service = createOpenVikingFeatureGateService({
      configPath,
      getPluginVersion: () => "2026.6.10",
      getOpenClawVersion: async () => "2026.4.8",
      getOpenClawVersionSync: () => "2026.4.8",
    });

    try {
      process.env.BUSINESS_CARRIER = "other, arkclaw_enterprise";
      await expect(service.getEnabledFeatureGates()).resolves.toEqual([
        OPENVIKING_260610_FEATURE,
      ]);

      process.env.BUSINESS_CARRIER = "other";
      await expect(service.getEnabledFeatureGates("arkclaw")).resolves.toEqual([
        OPENVIKING_260610_FEATURE,
      ]);
    } finally {
      if (previousCarrier === undefined) {
        delete process.env.BUSINESS_CARRIER;
      } else {
        process.env.BUSINESS_CARRIER = previousCarrier;
      }
    }
  });

  it("returns false for sync gate checks when config loading fails", () => {
    const service = createOpenVikingFeatureGateService({
      configPath: "/path/that/does/not/exist/feature-gates.json",
      getPluginVersion: () => "2026.6.10",
      getOpenClawVersion: async () => "2026.4.8",
      getOpenClawVersionSync: () => "2026.4.8",
    });

    expect(service.isFeatureGateEnabledSync(OPENVIKING_260610_FEATURE, "arkclaw")).toBe(false);
  });

  it("registers the Gateway RPC and returns enabled feature names", async () => {
    const configPath = writeFeatureGatesConfig({
      features: {
        [OPENVIKING_260610_FEATURE]: {
          enable: true,
          minPluginVersion: "2026.6.10",
          minOpenclawVersion: "2026.4.8",
          editions: ["arkclaw"],
        },
      },
    });
    const service = createOpenVikingFeatureGateService({
      configPath,
      getPluginVersion: () => "2026.6.10",
      getOpenClawVersion: async () => "2026.4.8",
      getOpenClawVersionSync: () => "2026.4.8",
    });
    const registered: Array<{
      name: string;
      handler: (input: {
        params?: unknown;
        respond: (success: boolean, data: unknown) => void;
      }) => Promise<void>;
    }> = [];
    const api = {
      registerGatewayMethod: vi.fn((name, handler) => {
        registered.push({ name, handler });
      }),
    };

    registerOpenVikingFeatureGatesMethod(api, service);

    expect(registered).toHaveLength(1);
    expect(registered[0]?.name).toBe(OPENVIKING_FEATURE_GATES_RPC);

    const respond = vi.fn();
    await registered[0]?.handler({ params: { edition: "arkclaw" }, respond });

    expect(respond).toHaveBeenCalledWith(true, {
      features: [OPENVIKING_260610_FEATURE],
    });
  });

  it("returns RPC failure when the feature gate config cannot be loaded", async () => {
    const service = createOpenVikingFeatureGateService({
      configPath: "/path/that/does/not/exist/feature-gates.json",
      getPluginVersion: () => "2026.6.10",
      getOpenClawVersion: async () => "2026.4.8",
      getOpenClawVersionSync: () => "2026.4.8",
    });
    const registered: Array<{
      handler: (input: {
        params?: unknown;
        respond: (success: boolean, data: unknown) => void;
      }) => Promise<void>;
    }> = [];

    registerOpenVikingFeatureGatesMethod(
      {
        registerGatewayMethod: vi.fn((_name, handler) => {
          registered.push({ handler });
        }),
      },
      service,
    );

    const respond = vi.fn();
    await registered[0]?.handler({ params: { edition: "arkclaw" }, respond });

    expect(respond).toHaveBeenCalledWith(false, expect.stringContaining("feature-gates.json"));
  });
});
