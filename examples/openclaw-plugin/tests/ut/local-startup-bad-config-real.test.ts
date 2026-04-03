import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { localClientCache, localClientPendingPromises } from "../../client.js";
import plugin from "../../index.js";

describe("local OpenViking startup with a bad config", () => {
  beforeEach(() => {
    localClientCache.clear();
    localClientPendingPromises.clear();
  });

  afterEach(() => {
    localClientCache.clear();
    localClientPendingPromises.clear();
  });

  it("fails startup quickly and keeps before_prompt_build non-blocking", async () => {
    const tempDir = await mkdtemp(join(tmpdir(), "ov-bad-conf-"));
    const badConfigPath = join(tempDir, "ov.conf");
    await writeFile(badConfigPath, "[broken\nthis is not valid\n", "utf8");

    try {
      const handlers = new Map<string, (event: unknown, ctx?: unknown) => unknown>();
      let service:
        | {
            start: () => Promise<void>;
            stop?: () => Promise<void> | void;
          }
        | null = null;
      const logs: Array<{ level: string; message: string }> = [];
      const logger = {
        debug: (message: string) => logs.push({ level: "debug", message }),
        error: (message: string) => logs.push({ level: "error", message }),
        info: (message: string) => logs.push({ level: "info", message }),
        warn: (message: string) => logs.push({ level: "warn", message }),
      };

      plugin.register({
        logger,
        on: (name, handler) => {
          handlers.set(name, handler);
        },
        pluginConfig: {
          autoCapture: true,
          autoRecall: true,
          configPath: badConfigPath,
          ingestReplyAssist: false,
          logFindRequests: false,
          mode: "local",
          port: 19439,
        },
        registerContextEngine: () => {},
        registerService: (entry) => {
          service = entry;
        },
        registerTool: () => {},
      });

      expect(service).toBeTruthy();
      const hook = handlers.get("before_prompt_build");
      expect(hook).toBeTruthy();

      const startAt = Date.now();
      const startOutcome = await Promise.race([
        service!.start().then(
          () => ({ kind: "resolved" as const }),
          (error) => ({ error: String(error), kind: "rejected" as const }),
        ),
        new Promise<{ kind: "timeout" }>((resolve) => {
          setTimeout(() => resolve({ kind: "timeout" }), 5_000);
        }),
      ]);

      expect(startOutcome.kind).toBe("rejected");
      expect(Date.now() - startAt).toBeLessThan(5_000);
      expect(logs.some((entry) => entry.message.includes("local mode marked unavailable"))).toBe(true);

      const hookAt = Date.now();
      const hookOutcome = await Promise.race([
        Promise.resolve(
          hook!(
            { messages: [{ content: "hello memory", role: "user" }], prompt: "hello memory" },
            { agentId: "main", sessionId: "test-session", sessionKey: "agent:main:test" },
          ),
        ).then(() => ({ kind: "returned" as const })),
        new Promise<{ kind: "timeout" }>((resolve) => {
          setTimeout(() => resolve({ kind: "timeout" }), 1_500);
        }),
      ]);

      expect(hookOutcome.kind).toBe("returned");
      expect(Date.now() - hookAt).toBeLessThan(1_500);
      expect(logs.some((entry) => entry.message.includes("failed to get client"))).toBe(true);

      await service?.stop?.();
    } finally {
      await rm(tempDir, { force: true, recursive: true });
    }
  }, 15_000);
});
