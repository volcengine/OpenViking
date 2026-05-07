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
