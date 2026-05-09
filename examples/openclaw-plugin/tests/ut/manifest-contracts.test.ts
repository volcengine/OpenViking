import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";

import contextEnginePlugin from "../../index.js";

const pluginRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const manifest = JSON.parse(
  readFileSync(resolve(pluginRoot, "openclaw.plugin.json"), "utf8"),
) as {
  activation?: { onStartup?: boolean; onCapabilities?: string[] };
  contracts?: { tools?: string[] };
};
const packageJson = JSON.parse(
  readFileSync(resolve(pluginRoot, "package.json"), "utf8"),
) as {
  files?: string[];
  scripts?: Record<string, string>;
};
const installManifest = JSON.parse(
  readFileSync(resolve(pluginRoot, "install-manifest.json"), "utf8"),
) as {
  compatibility?: { minOpenclawVersion?: string };
  files?: { required?: string[]; optional?: string[] };
  npm?: {
    build?: boolean;
    buildMinOpenclawVersion?: string;
    buildScript?: string;
    omitDev?: boolean;
    pruneAfterBuild?: boolean;
  };
};

function collectRegisteredToolNames(): string[] {
  const names: string[] = [];
  contextEnginePlugin.register({
    pluginConfig: {
      mode: "remote",
      baseUrl: "http://127.0.0.1:1933",
      autoCapture: false,
      autoRecall: false,
    },
    logger: {
      info: vi.fn(),
      warn: vi.fn(),
      error: vi.fn(),
      debug: vi.fn(),
    },
    registerTool: vi.fn((toolOrFactory: unknown) => {
      const tool =
        typeof toolOrFactory === "function"
          ? (toolOrFactory as (ctx: Record<string, unknown>) => { name: string })({
              sessionId: "contract-test-session",
            })
          : (toolOrFactory as { name: string });
      names.push(tool.name);
    }),
    registerCommand: vi.fn(),
    registerService: vi.fn(),
    registerContextEngine: vi.fn(),
    on: vi.fn(),
  } as any);
  return names.sort();
}

describe("OpenClaw 5.2 manifest contracts", () => {
  it("declares every runtime tool in contracts.tools", () => {
    expect(manifest.contracts?.tools?.toSorted()).toEqual(collectRegisteredToolNames());
  });

  it("opts into startup and capability-triggered hook/tool activation", () => {
    expect(manifest.activation?.onStartup).toBe(true);
    expect(manifest.activation?.onCapabilities?.toSorted()).toEqual(["hook", "tool"]);
  });
});

describe("OpenClaw 5.5 package runtime contract", () => {
  it("builds and publishes compiled runtime output for TypeScript entries", () => {
    expect(packageJson.scripts?.build).toBe("tsc -p tsconfig.build.json");
    expect(packageJson.scripts?.prepack).toBe("npm run build");
    expect(packageJson.files).toContain("dist/");
    expect(packageJson.files).toContain("install-manifest.json");
  });

  it("lets ov-install build runtime output from downloaded source", () => {
    expect(installManifest.npm).toMatchObject({
      build: true,
      buildMinOpenclawVersion: "2026.5.3",
      buildScript: "build",
      omitDev: true,
      pruneAfterBuild: true,
    });
    expect(installManifest.files?.required).toEqual(expect.arrayContaining([
      "index.ts",
      "commands/setup.ts",
      "tsconfig.json",
      "tsconfig.build.json",
      "package.json",
      "openclaw.plugin.json",
    ]));
    expect(installManifest.compatibility?.minOpenclawVersion).toBe("2026.4.8");
  });
});
