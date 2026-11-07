import { describe, expect, it, vi } from "vitest";

import type { OpenVikingClient } from "../../client.js";
import { memoryOpenVikingConfigSchema } from "../../config.js";
import { createMemoryOpenVikingContextEngine } from "../../context-engine.js";
import contextEnginePlugin from "../../index.js";
import {
  promptRecallHookGranted,
  registerOpenVikingPromptRecallHook,
} from "../../plugin/openviking-prompt-recall-hook.js";
import { recallForPromptHook } from "../../services/context-lifecycle-service.js";

const cfg = memoryOpenVikingConfigSchema.parse({
  contextManagementMode: "openviking",
  mode: "remote",
  baseUrl: "http://127.0.0.1:1933",
  autoCapture: false,
  autoRecall: true,
});
const memory = "User prefers Rust for backend tasks.";
const prompt = "what backend language should we use?";

function makeLogger() {
  return { info: vi.fn(), warn: vi.fn(), error: vi.fn(), debug: vi.fn() };
}

function makeClient() {
  return {
    healthCheck: vi.fn().mockResolvedValue(undefined),
    getSessionContext: vi.fn().mockResolvedValue(undefined),
    searchContext: vi.fn().mockResolvedValue({
      entries: [{ uri: "viking://user/default/memories/rust-pref", category: "preferences", text: memory, score: 0.93 }],
      rendered: `<memory>${memory}</memory>`,
      stats: { candidates: 1, used_tokens: 18 },
    }),
    read: vi.fn().mockResolvedValue(""),
  };
}

function registerHook(overrides: { autoRecall?: boolean; recall?: ReturnType<typeof vi.fn>; bypass?: boolean } = {}) {
  const on = vi.fn();
  const recall = overrides.recall ?? vi.fn().mockResolvedValue("<relevant-memories>Rust</relevant-memories>");
  const logger = makeLogger();
  registerOpenVikingPromptRecallHook({
    api: { on },
    cfg: { ...cfg, autoRecall: overrides.autoRecall ?? true },
    logger,
    getClient: vi.fn(),
    resolveAgentId: (sessionId: string) => `agent:${sessionId}`,
    isBypassedSession: () => overrides.bypass === true,
    recall,
  });
  const [hookName, handler] = on.mock.calls[0] as [string, (event: unknown, ctx?: unknown) => Promise<unknown>];
  return { hookName, handler, recall, logger };
}

describe("prompt recall hook grant", () => {
  it.each([
    [{ allowConversationAccess: true }, true],
    [{ allowConversationAccess: true, allowPromptInjection: false }, false],
    [{ allowPromptInjection: true }, false],
    [undefined, false],
  ])("reads plugins.entries.openviking.hooks %j", (hooks, granted) => {
    const config = { plugins: { entries: { openviking: hooks === undefined ? {} : { hooks } } } };
    expect(promptRecallHookGranted(config, "openviking")).toBe(granted);
  });

  it("is not granted without host config", () => {
    expect(promptRecallHookGranted(undefined, "openviking")).toBe(false);
  });
});

describe("before_prompt_build auto-recall", () => {
  it("prepends recall for the current request rather than the rebuilt prompt", async () => {
    const { hookName, handler, recall } = registerHook();
    expect(hookName).toBe("before_prompt_build");

    const result = await handler(
      { prompt: "history...\n" + prompt, currentUserMessage: prompt, currentUserMessageId: "m1", messages: [] },
      { sessionId: "s1", sessionKey: "agent:main:main", senderId: "u1" },
    );

    expect(result).toEqual({ prependContext: "<relevant-memories>Rust</relevant-memories>" });
    expect(recall).toHaveBeenCalledWith(expect.objectContaining({
      prompt,
      sessionId: "s1",
      sessionKey: "agent:main:main",
      runtimeContext: { senderId: "u1" },
    }));
  });

  it("falls back to the prompt on hosts without currentUserMessage", async () => {
    const { handler, recall } = registerHook();
    await handler({ prompt, messages: [] }, { sessionId: "s1" });
    expect(recall).toHaveBeenCalledWith(expect.objectContaining({ prompt }));
  });

  it("does not recall from history when the request has no text", async () => {
    const { handler, recall } = registerHook({ recall: vi.fn().mockResolvedValue("") });
    const result = await handler({ prompt, currentUserMessage: "", messages: [] }, { sessionId: "s1" });
    expect(recall).toHaveBeenCalledWith(expect.objectContaining({ prompt: "" }));
    expect(result).toBeUndefined();
  });

  it("reuses the first result when the same admission is rebuilt", async () => {
    const { handler, recall } = registerHook();
    const event = { prompt, currentUserMessage: prompt, currentUserMessageId: "m1", messages: [] };
    const first = await handler(event, { sessionId: "s1" });
    const retry = await handler(event, { sessionId: "s1" });
    await handler({ ...event, currentUserMessageId: "m2" }, { sessionId: "s1" });

    expect(retry).toEqual(first);
    expect(recall).toHaveBeenCalledTimes(2);
  });

  it.each([
    ["auto-recall disabled", { autoRecall: false }, { sessionId: "s1" }],
    ["bypassed session", { bypass: true }, { sessionId: "s1" }],
    ["no session", {}, {}],
  ])("skips recall for %s", async (_name, overrides, ctx) => {
    const { handler, recall } = registerHook(overrides);
    expect(await handler({ prompt, messages: [] }, ctx)).toBeUndefined();
    expect(recall).not.toHaveBeenCalled();
  });

  it("fails open when recall fails", async () => {
    const { handler, logger } = registerHook({ recall: vi.fn().mockRejectedValue(new Error("unavailable")) });
    expect(await handler({ prompt, messages: [] }, { sessionId: "s1" })).toBeUndefined();
    expect(logger.warn).toHaveBeenCalledWith(expect.stringContaining("auto-recall failed"));
  });

  it("keeps the default cooldown because prependContext is replayed with its turn", async () => {
    const client = makeClient();
    const block = await recallForPromptHook({
      cfg,
      logger: makeLogger(),
      getClient: vi.fn().mockResolvedValue(client as unknown as OpenVikingClient),
      resolveAgentId: (sessionId: string) => `agent:${sessionId}`,
      sessionId: "s1",
      prompt,
    });

    expect(block).toContain(memory);
    expect(client.searchContext).toHaveBeenCalledWith(prompt, expect.objectContaining({ dedupTurns: 5 }));
  });
});

describe("assemble with prompt-hook recall", () => {
  function makeEngine(profile = "") {
    const client = makeClient();
    client.read.mockResolvedValue(profile);
    const engine = createMemoryOpenVikingContextEngine({
      id: "openviking",
      name: "Context Engine (OpenViking)",
      cfg,
      logger: makeLogger(),
      getClient: vi.fn().mockResolvedValue(client as unknown as OpenVikingClient),
      resolveAgentId: (sessionId: string) => `agent:${sessionId}`,
      recallViaPromptHook: true,
    });
    return { engine, client };
  }

  it("leaves recall to the hook and keeps the profile", async () => {
    const { engine, client } = makeEngine("Name: Zayn");
    const messages = [{ role: "assistant", content: "previous answer" }];
    const result = await engine.assemble({ sessionId: "s1", messages, prompt, availableTools: new Set() } as never);

    expect(client.searchContext).not.toHaveBeenCalled();
    expect(result.messages).toBe(messages);
    expect(result.systemPromptAddition).toContain("Name: Zayn");
    expect(result.systemPromptAddition).not.toContain(memory);
  });

  it("does not inject recall into the transform-context tail", async () => {
    const { engine, client } = makeEngine();
    const messages = [{ role: "user", content: prompt }];
    const result = await engine.assemble({ sessionId: "s1", messages } as never);

    expect(client.searchContext).not.toHaveBeenCalled();
    expect(result.messages).toEqual(messages);
  });
});

describe("plugin registration", () => {
  function createPluginApi(hooks?: Record<string, unknown>) {
    return {
      id: "openviking",
      config: { plugins: { entries: { openviking: hooks ? { hooks } : {} } } },
      pluginConfig: { autoRecall: true },
      logger: makeLogger(),
      registerTool: vi.fn(),
      registerCommand: vi.fn(),
      registerService: vi.fn(),
      registerContextEngine: vi.fn(),
      registerGatewayMethod: vi.fn(),
      on: vi.fn(),
    };
  }
  const hookNames = (api: ReturnType<typeof createPluginApi>) => api.on.mock.calls.map(([name]) => name);

  it("recalls through before_prompt_build when conversation access is granted", () => {
    const api = createPluginApi({ allowConversationAccess: true });
    contextEnginePlugin.register(api as never);
    expect(hookNames(api)).toContain("before_prompt_build");
  });

  it("keeps recall on assemble without the grant", () => {
    const api = createPluginApi();
    contextEnginePlugin.register(api as never);
    expect(hookNames(api)).not.toContain("before_prompt_build");
    expect(api.logger.info).toHaveBeenCalledWith(expect.stringContaining("allowConversationAccess=true"));
  });
});
