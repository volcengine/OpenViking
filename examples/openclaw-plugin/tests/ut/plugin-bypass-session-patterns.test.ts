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
  it("bypasses before_prompt_build before any OV client work", async () => {
    const { handlers, logger } = setupPlugin({
      bypassSessionPatterns: ["agent:*:cron:**"],
    });

    const hook = handlers.get("before_prompt_build");
    expect(hook).toBeTruthy();

    const result = await hook!(
      {
        messages: [{ role: "user", content: "Alice: hi\nBob: hello" }],
        prompt: "Alice: hi\nBob: hello",
      },
      {
        sessionId: "runtime-session",
        sessionKey: "agent:main:cron:nightly:run:1",
      },
    );

    expect(result).toBeUndefined();
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
