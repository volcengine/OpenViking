import { describe, expect, it, vi } from "vitest";

import contextEnginePlugin from "../../index.js";

type HookHandler = (event: unknown, ctx?: Record<string, unknown>) => unknown;

function setupPlugin(pluginConfig?: Record<string, unknown>) {
  const handlers = new Map<string, HookHandler>();
  const logger = {
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
    debug: vi.fn(),
  };
  const registerContextEngine = vi.fn();

  contextEnginePlugin.register({
    logger,
    on: (name, handler) => {
      handlers.set(name, handler as HookHandler);
    },
    pluginConfig: {
      mode: "remote",
      baseUrl: "http://127.0.0.1:1933",
      autoCapture: true,
      autoRecall: true,
      ...pluginConfig,
    },
    registerContextEngine,
    registerService: vi.fn(),
    registerTool: vi.fn(),
  } as any);

  return {
    handlers,
    logger,
    registerContextEngine,
  };
}

describe("plugin bypass session patterns", () => {
  it("bypasses context-engine assemble before any OV client work", async () => {
    const { registerContextEngine, logger } = setupPlugin({
      bypassSessionPatterns: ["agent:*:cron:**"],
    });

    const factory = registerContextEngine.mock.calls[0]?.[1] as (() => {
      assemble: (params: {
        sessionId: string;
        sessionKey?: string;
        prompt?: string;
        messages: Array<{ role: string; content: string }>;
      }) => Promise<{ messages: Array<{ role: string; content: string }> }>;
    }) | undefined;
    expect(factory).toBeTruthy();
    const engine = factory!();
    const liveMessages = [{ role: "user", content: "Alice: hi\nBob: hello" }];

    const result = await engine.assemble({
      sessionId: "runtime-session",
      sessionKey: "agent:main:cron:nightly:run:1",
      prompt: "Alice: hi\nBob: hello",
      messages: liveMessages,
    });

    expect(result.messages).toBe(liveMessages);
    expect(logger.warn).not.toHaveBeenCalledWith(
      expect.stringContaining("failed to get client"),
    );
  });

  it("bypasses before_reset without calling commitOVSession", async () => {
    const { handlers, registerContextEngine } = setupPlugin({
      bypassSessionPatterns: ["agent:*:cron:**"],
    });

    const factory = registerContextEngine.mock.calls[0]?.[1] as (() => { commitOVSession: ReturnType<typeof vi.fn> }) | undefined;
    expect(factory).toBeTruthy();
    const engine = factory!();
    engine.commitOVSession = vi.fn().mockResolvedValue(true);

    const hook = handlers.get("before_reset");
    expect(hook).toBeTruthy();

    await hook!(
      {},
      {
        sessionId: "runtime-session",
        sessionKey: "agent:main:cron:nightly:run:1",
      },
    );

    expect(engine.commitOVSession).not.toHaveBeenCalled();
  });
});
