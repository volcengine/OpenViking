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
  configSchema?: { properties?: Record<string, unknown> };
};
const packageJson = JSON.parse(
  readFileSync(resolve(pluginRoot, "package.json"), "utf8"),
) as {
  engines?: { openclaw?: string };
  version?: string;
  files?: string[];
  openclaw?: {
    compat?: { pluginApi?: string; minGatewayVersion?: string };
    build?: { openclawVersion?: string; pluginSdkVersion?: string };
  };
  scripts?: Record<string, string>;
};
const installManifest = JSON.parse(
  readFileSync(resolve(pluginRoot, "install-manifest.json"), "utf8"),
) as {
  pluginVersion?: string;
  compatibility?: {
    minOpenclawVersion?: string;
    recommendedOpenclawVersion?: string;
    minOpenvikingVersion?: string;
    recommendedOpenvikingVersion?: string;
  };
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
      enableAddResourceTool: true,
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
  it("declares every registerable runtime tool in contracts.tools", () => {
    expect(manifest.contracts?.tools?.toSorted()).toEqual(collectRegisteredToolNames());
  });

  it("opts into startup and capability-triggered hook/tool activation", () => {
    expect(manifest.activation?.onStartup).toBe(true);
    expect(manifest.activation?.onCapabilities?.toSorted()).toEqual(["hook", "tool"]);
  });

  it("declares recall trace configuration schema keys", () => {
    expect(Object.keys(manifest.configSchema?.properties ?? {})).toEqual(expect.arrayContaining([
      "traceRecall",
      "traceRecallPersist",
      "traceRecallDir",
      "traceRecallRetentionDays",
      "traceRecallLoadRecentDays",
      "traceRecallMaxEntries",
      "traceRecallMaxResultsPerSearch",
      "traceRecallPreviewChars",
      "traceRecallQueryMaxChars",
      "traceRecallQueryMaxDays",
      "traceRecallIncludeContentByDefault",
      "traceRecallIncludeRawUserPreview",
      "recallTargetTypes",
    ]));
  });
});

describe("OpenClaw 5.5 package runtime contract", () => {
  it("builds and publishes compiled runtime output for TypeScript entries", () => {
    expect(packageJson.scripts?.build).toContain("rmSync('dist'");
    expect(packageJson.scripts?.build).toContain("tsc -p tsconfig.build.json");
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
      "recall-trace.ts",
      "commands/setup.ts",
      "tsconfig.json",
      "tsconfig.build.json",
      "package.json",
      "openclaw.plugin.json",
    ]));
    expect(installManifest.compatibility?.minOpenclawVersion).toBe("2026.5.27");
  });

  it("declares compatibility floors and recommended versions, and keeps version fields in sync", () => {
    expect(installManifest.compatibility).toMatchObject({
      minOpenclawVersion: "2026.5.27",
      recommendedOpenclawVersion: "2026.6.6",
      minOpenvikingVersion: "0.4.1",
      recommendedOpenvikingVersion: "0.4.1",
    });
    // OpenClaw installers read these package fields as host-version floor metadata.
    expect(packageJson.engines?.openclaw).toBe(">=2026.5.27");
    expect(packageJson.openclaw?.compat).toMatchObject({
      pluginApi: ">=2026.5.27",
      minGatewayVersion: "2026.5.27",
    });
    expect(packageJson.openclaw?.build).toMatchObject({
      openclawVersion: "2026.5.27",
      pluginSdkVersion: "2026.5.27",
    });
    // package.json version and install-manifest pluginVersion must stay identical.
    expect(installManifest.pluginVersion).toBe(packageJson.version);
  });
});
