import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as readline from "node:readline";
import { afterEach, describe, expect, it, vi } from "vitest";

import { __test__ as setupCommandTest } from "../../commands/setup.js";
import { defaultSetupIO } from "../../services/setup/config-writer.js";
import { createOpenVikingSetupService } from "../../services/setup/setup-flow.js";

vi.mock("node:readline", () => ({ createInterface: vi.fn() }));

describe("openviking setup agent prefix validation", () => {
  const tempDirs: string[] = [];

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    for (const dir of tempDirs.splice(0)) {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });

  it.each(["", "  ", "main", "foo_main", "foo-main", "Foo_123"])(
    "accepts valid agent prefix %j",
    (value) => {
      expect(/^[a-zA-Z0-9_-]*$/.test(value.trim())).toBe(true);
    },
  );

  it.each(["foo.bar", "foo/bar", "foo bar", "中文", "foo:bar"])(
    "rejects invalid agent prefix %j",
    (value) => {
      expect(/^[a-zA-Z0-9_-]*$/.test(value.trim())).toBe(false);
    },
  );

  it("uses sender as the canonical setup value and accepts legacy person input", () => {
    expect(setupCommandTest.normalizePeerRole("sender")).toBe("sender");
    expect(setupCommandTest.normalizePeerRole("person")).toBe("sender");
    expect(setupCommandTest.resolveSetupPeerRole("person")).toBe("sender");
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

  it("non-interactive setup can persist resource-only recallTargetTypes for post-install opt-in", async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "openviking-setup-"));
    tempDirs.push(dir);
    const configPath = path.join(dir, "openclaw.json");
    const { setupNonInteractive } = createOpenVikingSetupService({
      io: defaultSetupIO,
      checkServiceHealth: async () => ({
        ok: false,
        version: "",
        error: "offline",
        compatibility: "unknown",
        pluginVersion: "test",
        compatRange: "any",
      }),
      probeApiKeyType: async () => ({
        keyType: "unknown",
        needsAccountId: false,
        needsUserId: false,
        detail: "not probed",
      }),
    });

    const result = await setupNonInteractive(configPath, {
      baseUrl: "http://127.0.0.1:1933",
      allowOffline: true,
      recallTargetTypes: ["resource"],
    });

    const config = JSON.parse(fs.readFileSync(configPath, "utf-8"));
    expect(result.success).toBe(true);
    expect(config.plugins.entries.openviking.config.recallTargetTypes).toEqual(["resource"]);
  });
});
