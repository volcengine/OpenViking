import { EventEmitter } from "node:events";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("local OpenViking startup failure", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.resetModules();
  });

  it("fails fast when the child exits before health is ready", async () => {
    class FakeChild extends EventEmitter {
      stderr = new EventEmitter();
      killed = false;
      exitCode: number | null = null;
      signalCode: string | null = null;

      kill = vi.fn((signal: string = "SIGTERM") => {
        if (this.killed || this.exitCode !== null || this.signalCode !== null) {
          return true;
        }
        this.killed = true;
        this.signalCode = signal;
        this.emit("exit", null, signal);
        return true;
      });
    }

    const waitForHealth = vi.fn(() => new Promise<void>(() => {}));
    const prepareLocalPort = vi.fn(async () => 19433);
    const resolvePythonCommand = vi.fn(() => "python3");
    const spawn = vi.fn(() => {
      const child = new FakeChild();
      setTimeout(() => {
        if (!child.killed && child.exitCode === null && child.signalCode === null) {
          child.exitCode = 1;
          child.emit("exit", 1, null);
        }
      }, 20);
      return child;
    });

    vi.doMock("node:child_process", () => ({
      execSync: vi.fn(() => ""),
      spawn,
    }));

    vi.doMock("../../process-manager.js", async () => {
      const actual = await vi.importActual<typeof import("../../process-manager.js")>(
        "../../process-manager.js",
      );
      return {
        ...actual,
        IS_WIN: false,
        prepareLocalPort,
        resolvePythonCommand,
        waitForHealth,
      };
    });

    const { localClientCache, localClientPendingPromises } = await import("../../client.js");
    localClientCache.clear();
    localClientPendingPromises.clear();

    const { default: plugin } = await import("../../index.js");
    const handlers = new Map<string, (event: unknown, ctx?: unknown) => unknown>();
    let service:
      | {
          start: () => Promise<void>;
        }
      | null = null;
    const logs: Array<{ level: string; message: string }> = [];
    const unhandled: unknown[] = [];
    const onUnhandledRejection = (reason: unknown) => {
      unhandled.push(reason);
    };
    process.on("unhandledRejection", onUnhandledRejection);
    try {
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
          configPath: "/tmp/openclaw-ovbug/bad-ov.conf",
          logFindRequests: false,
          mode: "local",
          port: 19433,
        },
        registerContextEngine: vi.fn(),
        registerService: (entry) => {
          service = entry;
        },
        registerTool: vi.fn(),
      });

      expect(service).toBeTruthy();
      const hook = handlers.get("before_prompt_build");
      expect(hook).toBeTruthy();

      const startOutcome = await Promise.race([
        service!.start().then(
          () => ({ kind: "resolved" as const }),
          (error) => ({ error: String(error), kind: "rejected" as const }),
        ),
        new Promise<{ kind: "timeout" }>((resolve) => {
          setTimeout(() => resolve({ kind: "timeout" }), 500);
        }),
      ]);

      expect(startOutcome.kind).toBe("rejected");

      const hookOutcome = await Promise.race([
        Promise.resolve(
          hook!(
            { messages: [{ content: "hello memory", role: "user" }], prompt: "hello memory" },
            { agentId: "main", sessionId: "test-session", sessionKey: "agent:main:test" },
          ),
        ).then(() => ({ kind: "returned" as const })),
        new Promise<{ kind: "timeout" }>((resolve) => {
          setTimeout(() => resolve({ kind: "timeout" }), 500);
        }),
      ]);

      expect(hookOutcome.kind).toBe("returned");
      expect(logs.some((entry) => entry.message.includes("failed to get client"))).toBe(true);
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(unhandled).toEqual([]);
    } finally {
      process.off("unhandledRejection", onUnhandledRejection);
    }
  });
});
