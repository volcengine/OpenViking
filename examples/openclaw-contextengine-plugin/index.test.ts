import { beforeEach, describe, expect, it, vi } from "vitest";

const { createOpenVikingClient } = vi.hoisted(() => ({
  createOpenVikingClient: vi.fn(() => ({
    baseUrl: "http://127.0.0.1:1933",
    health: vi.fn(async () => true),
    find: vi.fn(async () => ({ memories: [] })),
    createSession: vi.fn(async () => "s1"),
    addSessionMessage: vi.fn(async () => undefined),
    commitSession: vi.fn(async () => ({ extractedCount: 0 })),
    deleteSession: vi.fn(async () => undefined),
  })),
}));

vi.mock("./client.js", () => ({
  createOpenVikingClient,
}));

import plugin from "./index.js";

describe("plugin scaffold", () => {
  beforeEach(() => {
    createOpenVikingClient.mockClear();
  });

  it("exports contextengine-openviking plugin id", () => {
    expect(plugin.id).toBe("contextengine-openviking");
    expect(plugin.kind).toBe("context-engine");
  });

  it("registers context engine factory", async () => {
    const registerContextEngine = vi.fn();

    await plugin.register?.({ registerContextEngine } as never);

    expect(registerContextEngine).toHaveBeenCalledWith(
      "contextengine-openviking",
      expect.any(Function),
    );
  });

  it("registers commit_memory and search_memories tools", async () => {
    const registerContextEngine = vi.fn();
    const registerTool = vi.fn();

    await plugin.register?.({ registerContextEngine, registerTool } as never);

    const names = registerTool.mock.calls.map((call) => call[0]?.name);
    expect(names).toContain("commit_memory");
    expect(names).toContain("search_memories");
  });

  it("builds OpenViking client from parsed plugin config", async () => {
    const registerContextEngine = vi.fn();

    await plugin.register?.({
      registerContextEngine,
      pluginConfig: {
        connection: {
          baseUrl: "http://ov.example:1933/",
          timeoutMs: 3200,
          apiKey: "test-key",
          agentId: "agent-alpha",
        },
      },
    } as never);

    expect(createOpenVikingClient).toHaveBeenCalledWith({
      baseUrl: "http://ov.example:1933",
      timeoutMs: 3200,
      apiKey: "test-key",
      agentId: "agent-alpha",
    });
  });
});
