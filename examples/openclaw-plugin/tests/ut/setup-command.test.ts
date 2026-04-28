import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { __test__ } from "../../commands/setup.js";

const originalFetch = globalThis.fetch;

describe("openviking setup remote tenant config", () => {
  let tempDir: string;
  let logSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(async () => {
    tempDir = await mkdtemp(join(tmpdir(), "openviking-setup-"));
    logSpy = vi.spyOn(console, "log").mockImplementation(() => undefined);
    globalThis.fetch = vi.fn(async () =>
      new Response(JSON.stringify({ version: "test" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    ) as unknown as typeof fetch;
  });

  afterEach(async () => {
    globalThis.fetch = originalFetch;
    logSpy.mockRestore();
    await rm(tempDir, { recursive: true, force: true });
  });

  it("writes accountId and userId when remote setup uses a root API key", async () => {
    const prompts: string[] = [];
    const answers = new Map([
      ["OpenViking server URL", "http://openviking.example"],
      ["API Key (optional)", "root-api-key"],
      ["Account ID (optional; required with root API keys for tenant-scoped APIs)", "default"],
      ["User ID (optional; required with root API keys for tenant-scoped APIs)", "default"],
      ["Agent ID (optional)", "coding-agent"],
    ]);

    await __test__.setupRemote(false, join(tempDir, "openclaw.json"), null, async (prompt, def = "") => {
      prompts.push(`${prompt}=${def}`);
      return answers.get(prompt) ?? def;
    });

    const saved = JSON.parse(await readFile(join(tempDir, "openclaw.json"), "utf8"));
    expect(saved.plugins.entries.openviking.config).toMatchObject({
      mode: "remote",
      baseUrl: "http://openviking.example",
      apiKey: "root-api-key",
      accountId: "default",
      userId: "default",
      agentId: "coding-agent",
    });
    expect(prompts).toContain(
      "Account ID (optional; required with root API keys for tenant-scoped APIs)=",
    );
    expect(prompts).toContain(
      "User ID (optional; required with root API keys for tenant-scoped APIs)=",
    );
  });

  it("preserves existing accountId and userId as remote setup defaults", async () => {
    const existing = {
      mode: "remote",
      baseUrl: "http://old.example",
      apiKey: "root-api-key",
      accountId: "tenant-a",
      userId: "alice",
      agentId: "agent-a",
    };
    const seenDefaults = new Map<string, string>();

    await __test__.setupRemote(false, join(tempDir, "openclaw.json"), existing, async (prompt, def = "") => {
      seenDefaults.set(prompt, def);
      return def;
    });

    expect(seenDefaults.get("Account ID (optional; required with root API keys for tenant-scoped APIs)")).toBe(
      "tenant-a",
    );
    expect(seenDefaults.get("User ID (optional; required with root API keys for tenant-scoped APIs)")).toBe(
      "alice",
    );

    const saved = JSON.parse(await readFile(join(tempDir, "openclaw.json"), "utf8"));
    expect(saved.plugins.entries.openviking.config.accountId).toBe("tenant-a");
    expect(saved.plugins.entries.openviking.config.userId).toBe("alice");
  });
});
