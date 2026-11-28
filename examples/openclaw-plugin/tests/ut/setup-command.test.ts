import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as readline from "node:readline";
import { afterEach, describe, expect, it, vi } from "vitest";

import { __test__ as setupCommandTest } from "../../commands/setup.js";

vi.mock("node:readline", () => ({ createInterface: vi.fn() }));

describe("openviking setup command", () => {
  const tempDirs: string[] = [];

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    for (const dir of tempDirs.splice(0)) {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });

  it("reads legacy person from existing config but rejects it as setup input", () => {
    expect(setupCommandTest.normalizePeerRole("sender")).toBe("sender");
    expect(setupCommandTest.normalizePeerRole("person")).toBe("sender");
    expect(setupCommandTest.resolveSetupPeerRole("sender")).toBe("sender");
    expect(() => setupCommandTest.resolveSetupPeerRole("person")).toThrow('"person" was renamed to "sender"');
  });

  it("interactive reconfiguration preserves the selected retention policy", async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "openviking-setup-"));
    tempDirs.push(dir);
    const configPath = path.join(dir, "openclaw.json");
    fs.writeFileSync(configPath, JSON.stringify({ plugins: { entries: { openviking: { config: {
      mode: "remote", baseUrl: "http://127.0.0.1:1",
      commitRetentionMode: "turn_budget", commitKeepRecentCount: 7,
    } } } } }));
    vi.stubEnv("OPENCLAW_STATE_DIR", dir);
    vi.resetModules();
    vi.spyOn(console, "log").mockImplementation(() => {});
    vi.mocked(readline.createInterface).mockReturnValue({
      question: (_prompt: string, reply: (answer: string) => void) => reply(""),
      close: vi.fn(),
    } as unknown as readline.Interface);
    const actions = new Map<string, (options: unknown) => Promise<void>>();
    let command = "";
    const program = {
      command(name: string) { command = name; return this; },
      description() { return this; },
      option() { return this; },
      action(handler: (options: unknown) => Promise<void>) { actions.set(command, handler); return this; },
    };
    const { registerSetupCli } = await import("../../commands/setup.js");
    registerSetupCli({ registerCli: (register: (args: unknown) => void) => register({ program }) });

    await actions.get("setup")!({ reconfigure: true });

    const config = JSON.parse(fs.readFileSync(configPath, "utf-8"));
    expect(config.plugins.entries.openviking.config).toMatchObject({
      commitRetentionMode: "turn_budget", commitKeepRecentCount: 7,
    });
  });

  it("non-interactive setup saves resource-only recall and replaces an occupied context engine only when forced", async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "openviking-setup-"));
    tempDirs.push(dir);
    const configPath = path.join(dir, "openclaw.json");
    fs.writeFileSync(configPath, JSON.stringify({
      plugins: { slots: { contextEngine: "other-engine" } },
    }));
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    const params = {
      baseUrl: "http://127.0.0.1:1933",
      allowOffline: true,
      recallTargetTypes: ["resource"],
      peerRole: "assistant" as const,
      peerPrefix: "main",
    };

    const regular = await setupCommandTest.setupNonInteractive(configPath, params);
    expect(regular).toMatchObject({
      success: false,
      action: "slot_blocked",
      slot: { activated: false, previousOwner: "other-engine", replaced: false },
    });
    const saved = JSON.parse(fs.readFileSync(configPath, "utf-8"));
    expect(saved.plugins).toMatchObject({
      allow: ["openviking"],
      slots: { contextEngine: "other-engine" },
      entries: { openviking: { config: {
        mode: "remote",
        baseUrl: "http://127.0.0.1:1933",
        peer_role: "assistant",
        peer_prefix: "main",
        recallTargetTypes: ["resource"],
      } } },
    });

    const forced = await setupCommandTest.setupNonInteractive(configPath, { ...params, forceSlot: true });
    expect(forced).toMatchObject({
      success: true,
      action: "configured",
      slot: { activated: true, previousOwner: "other-engine", replaced: true },
    });
    const updated = JSON.parse(fs.readFileSync(configPath, "utf-8"));
    expect(updated.plugins.slots.contextEngine).toBe("openviking");
    expect(updated.plugins.entries.openviking.config).toEqual(saved.plugins.entries.openviking.config);
  });
});
