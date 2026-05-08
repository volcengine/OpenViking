import { describe, expect, it, beforeEach, afterEach } from "vitest";
import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";
import { fileURLToPath } from "node:url";

import { __test__ } from "../../commands/setup.js";

const {
  parseVersionTuple,
  compareVersions,
  checkVersionCompatibility,
  isLegacyLocalMode,
  activateContextEngineSlot,
  isContextEngineSlotActive,
  ensureInstallRecord,
  findPluginPackageRoot,
  setExitCodeOnFailure,
} = __test__;

const pluginRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
let tmpDir: string;

beforeEach(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "ov-setup-test-"));
});

afterEach(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

function writeTmpConfig(data: Record<string, unknown>): string {
  const p = path.join(tmpDir, "openclaw.json");
  fs.writeFileSync(p, JSON.stringify(data, null, 2), "utf-8");
  return p;
}

function readTmpConfig(configPath: string): Record<string, unknown> {
  return JSON.parse(fs.readFileSync(configPath, "utf-8"));
}

// ---------------------------------------------------------------------------
// parseVersionTuple
// ---------------------------------------------------------------------------
describe("parseVersionTuple", () => {
  it("parses semver string", () => {
    expect(parseVersionTuple("1.2.3")).toEqual([1, 2, 3]);
  });

  it("strips leading v", () => {
    expect(parseVersionTuple("v0.5.0")).toEqual([0, 5, 0]);
  });

  it("strips pre-release suffix", () => {
    expect(parseVersionTuple("0.5.0-beta.1")).toEqual([0, 5, 0]);
  });

  it("handles two-part version", () => {
    expect(parseVersionTuple("2026.4")).toEqual([2026, 4]);
  });

  it("returns null for non-numeric input", () => {
    expect(parseVersionTuple("latest")).toBeNull();
  });

  it("treats empty string as [0] (Number('') === 0)", () => {
    expect(parseVersionTuple("")).toEqual([0]);
  });
});

// ---------------------------------------------------------------------------
// compareVersions
// ---------------------------------------------------------------------------
describe("compareVersions", () => {
  it("equal versions return 0", () => {
    expect(compareVersions([1, 2, 3], [1, 2, 3])).toBe(0);
  });

  it("greater major returns positive", () => {
    expect(compareVersions([2, 0, 0], [1, 9, 9])).toBeGreaterThan(0);
  });

  it("lesser minor returns negative", () => {
    expect(compareVersions([1, 0, 0], [1, 1, 0])).toBeLessThan(0);
  });

  it("handles different length tuples", () => {
    expect(compareVersions([1, 2], [1, 2, 0])).toBe(0);
    expect(compareVersions([1, 2], [1, 2, 1])).toBeLessThan(0);
  });
});

// ---------------------------------------------------------------------------
// checkVersionCompatibility
// ---------------------------------------------------------------------------
describe("checkVersionCompatibility", () => {
  it('returns "compatible" for in-range version', () => {
    expect(checkVersionCompatibility("0.5.0")).toBe("compatible");
  });

  it('returns "unknown" for empty version', () => {
    expect(checkVersionCompatibility("")).toBe("unknown");
  });

  it('returns "unknown" for non-parseable version', () => {
    expect(checkVersionCompatibility("not-a-version")).toBe("unknown");
  });

  it('returns "server_too_old" below the manifest minimum version', () => {
    expect(checkVersionCompatibility("0.1.0")).toBe("server_too_old");
  });
});

describe("setup runtime package metadata", () => {
  it("resolves the package root from source and compiled command locations", () => {
    expect(findPluginPackageRoot(path.join(pluginRoot, "commands"))).toBe(pluginRoot);
    expect(findPluginPackageRoot(path.join(pluginRoot, "dist", "commands"))).toBe(pluginRoot);
  });
});

describe("setup command exit status", () => {
  it("marks failed setup results as process failures", () => {
    const previousExitCode = process.exitCode;
    try {
      process.exitCode = undefined;
      setExitCodeOnFailure({ success: false });
      expect(process.exitCode).toBe(1);
    } finally {
      process.exitCode = previousExitCode;
    }
  });
});

// ---------------------------------------------------------------------------
// isLegacyLocalMode
// ---------------------------------------------------------------------------
describe("isLegacyLocalMode", () => {
  it("returns true for mode=local", () => {
    expect(isLegacyLocalMode({ mode: "local" })).toBe(true);
  });

  it("returns true when mode is missing", () => {
    expect(isLegacyLocalMode({})).toBe(true);
  });

  it("returns false for mode=remote", () => {
    expect(isLegacyLocalMode({ mode: "remote" })).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// activateContextEngineSlot + isContextEngineSlotActive
// ---------------------------------------------------------------------------
describe("activateContextEngineSlot", () => {
  it("activates slot on fresh config", () => {
    const p = writeTmpConfig({});
    const result = activateContextEngineSlot(p);
    expect(result.activated).toBe(true);
    expect(result.replaced).toBe(false);
    expect(isContextEngineSlotActive(p)).toBe(true);
  });

  it("no-ops when slot already set to openviking", () => {
    const p = writeTmpConfig({
      plugins: { slots: { contextEngine: "openviking" } },
    });
    const result = activateContextEngineSlot(p);
    expect(result.activated).toBe(false);
    expect(result.replaced).toBe(false);
  });

  it("refuses to overwrite another plugin without force", () => {
    const p = writeTmpConfig({
      plugins: { slots: { contextEngine: "other-plugin" } },
    });
    const result = activateContextEngineSlot(p);
    expect(result.activated).toBe(false);
    expect(result.previousOwner).toBe("other-plugin");
    expect(result.replaced).toBe(false);
    const cfg = readTmpConfig(p);
    expect((cfg.plugins as Record<string, unknown>).slots).toEqual({
      contextEngine: "other-plugin",
    });
  });

  it("overwrites another plugin with force=true", () => {
    const p = writeTmpConfig({
      plugins: { slots: { contextEngine: "other-plugin" } },
    });
    const result = activateContextEngineSlot(p, true);
    expect(result.activated).toBe(true);
    expect(result.previousOwner).toBe("other-plugin");
    expect(result.replaced).toBe(true);
    expect(isContextEngineSlotActive(p)).toBe(true);
  });

  it("creates config file when it does not exist", () => {
    const p = path.join(tmpDir, "nonexistent.json");
    const result = activateContextEngineSlot(p);
    expect(result.activated).toBe(true);
    expect(fs.existsSync(p)).toBe(true);
  });
});

describe("isContextEngineSlotActive", () => {
  it("returns false for empty config", () => {
    const p = writeTmpConfig({});
    expect(isContextEngineSlotActive(p)).toBe(false);
  });

  it("returns false for nonexistent file", () => {
    const p = path.join(tmpDir, "missing.json");
    expect(isContextEngineSlotActive(p)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// ensureInstallRecord
// ---------------------------------------------------------------------------
describe("ensureInstallRecord", () => {
  it("creates allow entries when missing", () => {
    const plugins: Record<string, unknown> = {};
    ensureInstallRecord(plugins);
    expect(plugins.installs).toBeUndefined();
    expect(plugins.allow).toEqual(["openviking"]);
  });

  it("preserves unrelated installs and allow entries", () => {
    const plugins: Record<string, unknown> = {
      installs: { mem0: { source: "npm", spec: "@mem0/openclaw-mem0" } },
      allow: ["mem0"],
    };
    ensureInstallRecord(plugins);
    expect((plugins.installs as Record<string, unknown>).mem0).toEqual({
      source: "npm",
      spec: "@mem0/openclaw-mem0",
    });
    expect((plugins.installs as Record<string, unknown>).openviking).toBeUndefined();
    expect(plugins.allow).toEqual(["mem0", "openviking"]);
  });

  it("removes stale openviking install records and does not duplicate allow entries", () => {
    const plugins: Record<string, unknown> = {
      installs: { openviking: { npm: "@openclaw/openviking" } },
      allow: ["openviking"],
    };
    ensureInstallRecord(plugins);
    expect((plugins.installs as Record<string, unknown>).openviking).toBeUndefined();
    expect((plugins.allow as string[]).filter((x) => x === "openviking")).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// Slot protection: non-interactive default behavior
// (activateContextEngineSlot with force=false should NOT overwrite)
// ---------------------------------------------------------------------------
describe("activateContextEngineSlot — non-interactive default (force=false)", () => {
  it("activates slot on empty config (no conflict)", () => {
    const p = writeTmpConfig({});
    const result = activateContextEngineSlot(p, false);
    expect(result.activated).toBe(true);
    expect(result.replaced).toBe(false);
    expect(isContextEngineSlotActive(p)).toBe(true);
  });

  it("blocks when slot is owned by another plugin", () => {
    const p = writeTmpConfig({
      plugins: { slots: { contextEngine: "mem0" } },
    });
    const result = activateContextEngineSlot(p, false);
    expect(result.activated).toBe(false);
    expect(result.previousOwner).toBe("mem0");
    expect(result.replaced).toBe(false);
    const cfg = readTmpConfig(p);
    expect((cfg.plugins as Record<string, unknown>).slots).toEqual({
      contextEngine: "mem0",
    });
  });

  it("replaces when force=true is passed", () => {
    const p = writeTmpConfig({
      plugins: { slots: { contextEngine: "mem0" } },
    });
    const result = activateContextEngineSlot(p, true);
    expect(result.activated).toBe(true);
    expect(result.previousOwner).toBe("mem0");
    expect(result.replaced).toBe(true);
    expect(isContextEngineSlotActive(p)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// checkVersionCompatibility edge cases
// ---------------------------------------------------------------------------
describe("checkVersionCompatibility — edge cases", () => {
  it("pre-release suffix is stripped before comparison", () => {
    expect(checkVersionCompatibility("0.5.0-beta.1")).toBe("compatible");
  });

  it("handles large year-based version numbers", () => {
    expect(checkVersionCompatibility("2026.4.33")).toBe("compatible");
  });
});
