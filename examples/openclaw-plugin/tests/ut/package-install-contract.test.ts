import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const rootDir = join(dirname(fileURLToPath(import.meta.url)), "../..");

function readJson(path: string): any {
  return JSON.parse(readFileSync(join(rootDir, path), "utf8"));
}

describe("OpenClaw plugin package and install contract", () => {
  it("uses compiled OpenClaw runtime entries", () => {
    const packageJson = readJson("package.json");

    expect(packageJson.openclaw.extensions).toEqual(["./dist/index.js"]);
    expect(packageJson.openclaw.setupEntry).toBe("./dist/commands/setup.js");
    expect(packageJson.scripts.build).toBe("tsc -p tsconfig.build.json");
  });

  it("keeps source install manifest aligned with required source files", () => {
    const installManifest = readJson("install-manifest.json");

    expect(installManifest.plugin).toMatchObject({
      id: "openviking",
      kind: "context-engine",
      slot: "contextEngine",
    });
    expect(installManifest.files.required).toEqual(expect.arrayContaining([
      "index.ts",
      "config.ts",
      "context-engine.ts",
      "auto-recall.ts",
      "client.ts",
      "process-manager.ts",
      "memory-ranking.ts",
      "token-estimator.ts",
      "text-utils.ts",
      "tool-call-id.ts",
      "session-transcript-repair.ts",
      "runtime-utils.ts",
      "recall-trace.ts",
      "commands/setup.ts",
      "tsconfig.json",
      "tsconfig.build.json",
      "package.json",
      "openclaw.plugin.json",
    ]));
    for (const file of installManifest.files.required) {
      expect(existsSync(join(rootDir, file)), `${file} should exist`).toBe(true);
    }
  });

  it("ships npm package files needed by source and compiled installs", () => {
    const packageJson = readJson("package.json");

    expect(packageJson.files).toEqual(expect.arrayContaining([
      "dist/",
      "*.ts",
      "commands/setup.ts",
      "install-manifest.json",
      "openclaw.plugin.json",
      "package.json",
      "README.md",
      "INSTALL.md",
      "INSTALL-ZH.md",
      "INSTALL-AGENT.md",
      "images/",
      "skills/",
    ]));
  });

  it("keeps runtime dependencies and overrides available for installed plugin loading", () => {
    const packageJson = readJson("package.json");

    expect(packageJson.dependencies["@sinclair/typebox"]).toBeDefined();
    expect(packageJson.dependencies.fflate).toBeDefined();
    expect(packageJson.overrides?.axios).toMatch(/^\^1\./);
    expect(packageJson.devDependencies.typescript).toBeDefined();
    expect(packageJson.devDependencies.vitest).toBeDefined();
  });

  it("keeps setup helper fallback metadata compatible with the current plugin manifest", () => {
    const installHelper = readFileSync(join(rootDir, "setup-helper/install.js"), "utf8");
    const installManifest = readJson("install-manifest.json");

    expect(installHelper).toContain("FALLBACK_CURRENT");
    expect(installHelper).toContain(`id: "${installManifest.plugin.id}"`);
    expect(installHelper).toContain(`kind: "${installManifest.plugin.kind}"`);
    expect(installHelper).toContain(`slot: "${installManifest.plugin.slot}"`);
    expect(installHelper).toContain("OPENVIKING_PEER_ROLE");
    expect(installHelper).toContain("OPENVIKING_PEER_PREFIX");
    expect(installHelper).not.toContain("OPENVIKING_AGENT_PREFIX");
  });
});
