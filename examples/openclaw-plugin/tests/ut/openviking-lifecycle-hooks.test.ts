import { describe, expect, it, vi } from "vitest";

import type { OpenVikingClient } from "../../client.js";
import { memoryOpenVikingConfigSchema } from "../../config.js";
import { createMemoryOpenVikingContextEngine } from "../../context-engine.js";
import { registerOpenVikingLifecycleHooks } from "../../plugin/openviking-lifecycle-hooks.js";
import { openClawSessionToOvStorageId } from "../../routing/identity-routing.js";

describe("OpenViking lifecycle hooks", () => {
  it("finalizes session_end with a non-blocking full commit", async () => {
    const handlers = new Map<string, (event: unknown, ctx?: Record<string, string>) => unknown>();
    const commitOVSession = vi.fn().mockResolvedValue(true);
    const rememberSessionAgentId = vi.fn();

    registerOpenVikingLifecycleHooks({
      api: {
        on: vi.fn((hookName, handler) => {
          handlers.set(hookName, handler);
        }),
      },
      rememberSessionAgentId,
      toOVSessionId: (sessionId, sessionKey) => sessionKey ?? sessionId,
      isBypassedSession: () => false,
      verboseRoutingInfo: vi.fn(),
      getContextEngine: () => ({ commitOVSession }),
      logger: { info: vi.fn(), warn: vi.fn() },
    });

    const context = {
      agentId: "main",
      sessionId: "session-old",
      sessionKey: "agent:main:main",
    };
    await handlers.get("session_end")?.({}, context);

    expect(rememberSessionAgentId).toHaveBeenCalledWith(context);
    expect(commitOVSession).toHaveBeenCalledWith(
      { sessionId: "session-old", sessionKey: "agent:main:main" },
      { wait: false, keepRecentCount: 0 },
    );
  });

  it("forwards final commit options through the real context engine", async () => {
    const handlers = new Map<string, (event: unknown, ctx?: Record<string, string>) => unknown>();
    const commitSession = vi.fn().mockResolvedValue({
      status: "accepted",
      archived: true,
      task_id: "task-finalize",
    });
    const cfg = memoryOpenVikingConfigSchema.parse({
      mode: "remote",
      baseUrl: "http://127.0.0.1:1933",
      autoCapture: false,
      autoRecall: false,
    });
    const logger = { info: vi.fn(), warn: vi.fn(), error: vi.fn() };
    const contextEngine = createMemoryOpenVikingContextEngine({
      id: "openviking",
      name: "Test Engine",
      version: "test",
      cfg,
      logger,
      getClient: vi.fn().mockResolvedValue({ commitSession } as unknown as OpenVikingClient),
      resolveAgentId: () => "main",
    });

    registerOpenVikingLifecycleHooks({
      api: {
        on: vi.fn((hookName, handler) => {
          handlers.set(hookName, handler);
        }),
      },
      rememberSessionAgentId: vi.fn(),
      toOVSessionId: openClawSessionToOvStorageId,
      isBypassedSession: () => false,
      verboseRoutingInfo: vi.fn(),
      getContextEngine: () => contextEngine,
      logger,
    });

    const context = { sessionId: "plain-session", sessionKey: "agent:main:main" };
    await handlers.get("session_end")?.({}, context);

    expect(commitSession).toHaveBeenCalledWith(
      openClawSessionToOvStorageId(context.sessionId, context.sessionKey),
      { wait: false, keepRecentCount: 0 },
    );
  });

  it.each([
    ["missing session ID", {}, () => ({ commitOVSession: vi.fn() })],
    ["missing context engine", { sessionId: "session-without-engine" }, () => null],
  ])("skips finalization with %s", async (_label, context, getContextEngine) => {
    const handlers = new Map<string, (event: unknown, ctx?: Record<string, string>) => unknown>();
    const toOVSessionId = vi.fn((sessionId: string) => sessionId);

    registerOpenVikingLifecycleHooks({
      api: {
        on: vi.fn((hookName, handler) => {
          handlers.set(hookName, handler);
        }),
      },
      rememberSessionAgentId: vi.fn(),
      toOVSessionId,
      isBypassedSession: () => false,
      verboseRoutingInfo: vi.fn(),
      getContextEngine,
      logger: { info: vi.fn(), warn: vi.fn() },
    });

    await handlers.get("session_end")?.({}, context);

    expect(toOVSessionId).not.toHaveBeenCalled();
  });

  it("deduplicates overlapping reset and session_end finalization", async () => {
    const handlers = new Map<string, (event: unknown, ctx?: Record<string, string>) => unknown>();
    let finishCommit: ((value: boolean) => void) | undefined;
    const commitOVSession = vi.fn(() => new Promise<boolean>((resolve) => {
      finishCommit = resolve;
    }));

    registerOpenVikingLifecycleHooks({
      api: {
        on: vi.fn((hookName, handler) => {
          handlers.set(hookName, handler);
        }),
      },
      rememberSessionAgentId: vi.fn(),
      toOVSessionId: (sessionId, sessionKey) => sessionKey ?? sessionId,
      isBypassedSession: () => false,
      verboseRoutingInfo: vi.fn(),
      getContextEngine: () => ({ commitOVSession }),
      logger: { info: vi.fn(), warn: vi.fn() },
    });

    const resetCommit = handlers.get("before_reset")?.({}, {
      sessionId: "session-old-a",
      sessionKey: "agent:main:main",
    });
    const endCommit = handlers.get("session_end")?.({}, {
      sessionId: "session-old-b",
      sessionKey: "agent:main:main",
    });
    await vi.waitFor(() => expect(commitOVSession).toHaveBeenCalledTimes(1));
    finishCommit?.(true);
    await Promise.all([resetCommit, endCommit]);

    expect(commitOVSession).toHaveBeenCalledTimes(1);
    expect(commitOVSession).toHaveBeenCalledWith(
      { sessionId: "session-old-a", sessionKey: "agent:main:main" },
      { wait: false, keepRecentCount: 0 },
    );
  });

  it("does not finalize bypassed session_end events", async () => {
    const handlers = new Map<string, (event: unknown, ctx?: Record<string, string>) => unknown>();
    const commitOVSession = vi.fn();

    registerOpenVikingLifecycleHooks({
      api: {
        on: vi.fn((hookName, handler) => {
          handlers.set(hookName, handler);
        }),
      },
      rememberSessionAgentId: vi.fn(),
      toOVSessionId: (sessionId, sessionKey) => sessionKey ?? sessionId,
      isBypassedSession: (ctx) => ctx?.sessionId === "session-bypassed",
      verboseRoutingInfo: vi.fn(),
      getContextEngine: () => ({ commitOVSession }),
      logger: { info: vi.fn(), warn: vi.fn() },
    });

    await handlers.get("session_end")?.({}, {
      sessionId: "session-normal",
      sessionKey: "agent:main:main",
    });
    await handlers.get("session_end")?.({}, {
      sessionId: "session-bypassed",
      sessionKey: "agent:main:cron:job",
    });

    expect(commitOVSession).toHaveBeenCalledTimes(1);
    expect(commitOVSession).toHaveBeenCalledWith(
      { sessionId: "session-normal", sessionKey: "agent:main:main" },
      { wait: false, keepRecentCount: 0 },
    );
  });

  it("retries a shared rejection from the waiting lifecycle event", async () => {
    const handlers = new Map<string, (event: unknown, ctx?: Record<string, string>) => unknown>();
    let rejectCommit: ((reason: Error) => void) | undefined;
    const commitOVSession = vi.fn()
      .mockImplementationOnce(() => new Promise<boolean>((_resolve, reject) => {
        rejectCommit = reject;
      }))
      .mockResolvedValueOnce(true);
    const logger = { info: vi.fn(), warn: vi.fn() };

    registerOpenVikingLifecycleHooks({
      api: {
        on: vi.fn((hookName, handler) => {
          handlers.set(hookName, handler);
        }),
      },
      rememberSessionAgentId: vi.fn(),
      toOVSessionId: (sessionId, sessionKey) => sessionKey ?? sessionId,
      isBypassedSession: () => false,
      verboseRoutingInfo: vi.fn(),
      getContextEngine: () => ({ commitOVSession }),
      logger,
    });

    const context = { sessionId: "session-retry", sessionKey: "agent:main:main" };
    const resetCommit = handlers.get("before_reset")?.({}, context);
    const endCommit = handlers.get("session_end")?.({}, context);
    await vi.waitFor(() => expect(commitOVSession).toHaveBeenCalledTimes(1));
    rejectCommit?.(new Error("commit unavailable"));
    await expect(Promise.all([resetCommit, endCommit])).resolves.toEqual([undefined, undefined]);

    expect(commitOVSession).toHaveBeenCalledTimes(2);
    expect(logger.warn).toHaveBeenCalledWith(
      "openviking: failed to commit OV session on before_reset: Error: commit unavailable",
    );
    expect(logger.info).toHaveBeenCalledWith(
      "openviking: committed OV session on session_end for session=session-retry",
    );
  });

  it("does not deduplicate distinct OV sessions that share a raw session ID", async () => {
    const handlers = new Map<string, (event: unknown, ctx?: Record<string, string>) => unknown>();
    const pendingCommits: Array<(value: boolean) => void> = [];
    const commitOVSession = vi.fn(() => new Promise<boolean>((resolve) => {
      pendingCommits.push(resolve);
    }));

    registerOpenVikingLifecycleHooks({
      api: {
        on: vi.fn((hookName, handler) => {
          handlers.set(hookName, handler);
        }),
      },
      rememberSessionAgentId: vi.fn(),
      toOVSessionId: (sessionId, sessionKey) => sessionKey ?? sessionId,
      isBypassedSession: () => false,
      verboseRoutingInfo: vi.fn(),
      getContextEngine: () => ({ commitOVSession }),
      logger: { info: vi.fn(), warn: vi.fn() },
    });

    const first = handlers.get("session_end")?.({}, {
      sessionId: "shared-raw-id",
      sessionKey: "agent:main:first",
    });
    const second = handlers.get("session_end")?.({}, {
      sessionId: "shared-raw-id",
      sessionKey: "agent:main:second",
    });
    await vi.waitFor(() => expect(commitOVSession).toHaveBeenCalledTimes(2));
    for (const resolve of pendingCommits) resolve(true);
    await Promise.all([first, second]);

    expect(commitOVSession).toHaveBeenCalledTimes(2);
  });

  it("clears successful finalization so a later event can commit new messages", async () => {
    const handlers = new Map<string, (event: unknown, ctx?: Record<string, string>) => unknown>();
    const commitOVSession = vi.fn().mockResolvedValue(true);

    registerOpenVikingLifecycleHooks({
      api: {
        on: vi.fn((hookName, handler) => {
          handlers.set(hookName, handler);
        }),
      },
      rememberSessionAgentId: vi.fn(),
      toOVSessionId: (sessionId, sessionKey) => sessionKey ?? sessionId,
      isBypassedSession: () => false,
      verboseRoutingInfo: vi.fn(),
      getContextEngine: () => ({ commitOVSession }),
      logger: { info: vi.fn(), warn: vi.fn() },
    });

    const context = { sessionId: "session-reused", sessionKey: "agent:main:main" };
    await handlers.get("before_reset")?.({}, context);
    await handlers.get("session_end")?.({}, context);

    expect(commitOVSession).toHaveBeenCalledTimes(2);
  });

  it("clears a resolved failure so a later event can retry", async () => {
    const handlers = new Map<string, (event: unknown, ctx?: Record<string, string>) => unknown>();
    const commitOVSession = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);

    registerOpenVikingLifecycleHooks({
      api: {
        on: vi.fn((hookName, handler) => {
          handlers.set(hookName, handler);
        }),
      },
      rememberSessionAgentId: vi.fn(),
      toOVSessionId: (sessionId, sessionKey) => sessionKey ?? sessionId,
      isBypassedSession: () => false,
      verboseRoutingInfo: vi.fn(),
      getContextEngine: () => ({ commitOVSession }),
      logger: { info: vi.fn(), warn: vi.fn() },
    });

    const context = { sessionId: "session-false", sessionKey: "agent:main:main" };
    await handlers.get("session_end")?.({}, context);
    await handlers.get("session_end")?.({}, context);

    expect(commitOVSession).toHaveBeenCalledTimes(2);
  });

  it("handles a synchronous commit throw and allows retry", async () => {
    const handlers = new Map<string, (event: unknown, ctx?: Record<string, string>) => unknown>();
    const commitOVSession = vi.fn()
      .mockImplementationOnce(() => {
        throw new Error("synchronous failure");
      })
      .mockResolvedValueOnce(true);
    const logger = { info: vi.fn(), warn: vi.fn() };

    registerOpenVikingLifecycleHooks({
      api: {
        on: vi.fn((hookName, handler) => {
          handlers.set(hookName, handler);
        }),
      },
      rememberSessionAgentId: vi.fn(),
      toOVSessionId: (sessionId, sessionKey) => sessionKey ?? sessionId,
      isBypassedSession: () => false,
      verboseRoutingInfo: vi.fn(),
      getContextEngine: () => ({ commitOVSession }),
      logger,
    });

    const context = { sessionId: "session-sync-throw", sessionKey: "agent:main:main" };
    await handlers.get("session_end")?.({}, context);
    await handlers.get("session_end")?.({}, context);

    expect(commitOVSession).toHaveBeenCalledTimes(2);
    expect(logger.warn).toHaveBeenCalledWith(
      "openviking: failed to commit OV session on session_end: Error: synchronous failure",
    );
  });
});
