import { describe, expect, it, vi } from "vitest";

import {
  isCanonicalExperienceUri,
  registerOpenVikingExperienceTools,
} from "../../plugin/openviking-experience-tools.js";

type RegisteredTool = {
  name: string;
  execute: (toolCallId: string, params: Record<string, unknown>) => Promise<unknown>;
};

function createHarness(options: { bypassed?: boolean; userId?: string } = {}) {
  const factories = new Map<string, (ctx: Record<string, unknown>) => RegisteredTool>();
  const client = {
    find: vi.fn(),
    read: vi.fn(),
  };
  registerOpenVikingExperienceTools({
    registerTool(factory, registration) {
      factories.set(registration.name, factory as (ctx: Record<string, unknown>) => RegisteredTool);
    },
    getClient: vi.fn().mockResolvedValue(client),
    resolvePluginSessionRouting: vi.fn().mockReturnValue({
      agentId: "agent-main",
      actorPeerId: "peer-main",
    }),
    isBypassedSession: vi.fn().mockReturnValue(options.bypassed ?? false),
    makeBypassedToolResult: (toolName) => ({ bypassed: toolName }),
    userId: options.userId,
  });
  const tool = (name: string): RegisteredTool => {
    const factory = factories.get(name);
    if (!factory) throw new Error(`tool ${name} was not registered`);
    return factory({ sessionId: "session-1" });
  };
  return { client, factories, tool };
}

function parseTextResult(value: unknown): Record<string, unknown> {
  const result = value as { content: Array<{ type: string; text: string }> };
  return JSON.parse(result.content[0]!.text) as Record<string, unknown>;
}

describe("OpenViking Experience tools", () => {
  it("registers the two standard Experience tool names", () => {
    const { factories } = createHarness();
    expect([...factories.keys()]).toEqual(["search_experience", "read_experience"]);
  });

  it("searches only the current-user Experience directory and returns canonical results", async () => {
    const { client, tool } = createHarness();
    client.find.mockResolvedValue({
      memories: [
        {
          uri: "viking://user/test/memories/experiences/无订单号换货处理.md",
          score: 0.86,
          abstract: "先确认用户身份，再定位订单。",
        },
        {
          uri: "viking://user/test/memories/experiences/.overview.md",
          score: 0.9,
        },
        {
          uri: "viking://user/test/memories/preferences/换货偏好.md",
          score: 0.8,
        },
      ],
      resources: [],
      skills: [],
      total: 3,
    });

    const output = await tool("search_experience").execute("call-search", {
      query: "没有订单号时如何换货",
      limit: 7,
    });

    expect(client.find).toHaveBeenCalledWith(
      "没有订单号时如何换货",
      {
        targetUri: "viking://user/memories/experiences/",
        limit: 7,
      },
      "peer-main",
    );
    expect(parseTextResult(output)).toEqual({
      results: [
        {
          uri: "viking://user/test/memories/experiences/无订单号换货处理.md",
          title: "无订单号换货处理",
          score: 0.86,
          snippet: "先确认用户身份，再定位订单。",
        },
      ],
    });
  });

  it("reads a canonical Experience and preserves its URI in the tool result", async () => {
    const { client, tool } = createHarness();
    const uri = "viking://user/test/memories/experiences/无订单号换货处理.md";
    client.read.mockResolvedValue("# 无订单号换货处理\n\n先验证用户身份。");

    const output = await tool("read_experience").execute("call-read", { uri });

    expect(client.read).toHaveBeenCalledWith(uri, "peer-main");
    expect(parseTextResult(output)).toEqual({
      uri,
      content: "# 无订单号换货处理\n\n先验证用户身份。",
    });
  });

  it("rejects non-Experience and noncanonical URIs before reading", async () => {
    const { client, tool } = createHarness();
    const readTool = tool("read_experience");

    await expect(readTool.execute("call-read", {
      uri: "viking://user/test/memories/preferences/换货偏好.md",
    })).rejects.toThrow("canonical OpenViking Experience URI");
    await expect(readTool.execute("call-read", {
      uri: "viking://user/test/memories/experiences/a.md#approach",
    })).rejects.toThrow("canonical OpenViking Experience URI");
    expect(client.read).not.toHaveBeenCalled();
  });

  it("rejects another user's Experience when userId is explicitly configured", async () => {
    const { client, tool } = createHarness({ userId: "current-user" });

    await expect(tool("read_experience").execute("call-read", {
      uri: "viking://user/other-user/memories/experiences/a.md",
    })).rejects.toThrow("canonical OpenViking Experience URI");
    expect(client.read).not.toHaveBeenCalled();
  });

  it("uses the standard bypass result without calling OpenViking", async () => {
    const { client, tool } = createHarness({ bypassed: true });

    await expect(tool("search_experience").execute("call-search", { query: "换货" }))
      .resolves.toEqual({ bypassed: "search_experience" });
    await expect(tool("read_experience").execute("call-read", {
      uri: "viking://user/test/memories/experiences/a.md",
    })).resolves.toEqual({ bypassed: "read_experience" });
    expect(client.find).not.toHaveBeenCalled();
    expect(client.read).not.toHaveBeenCalled();
  });
});

describe("isCanonicalExperienceUri", () => {
  it("accepts Experience files and rejects sidecars or URI aliases", () => {
    expect(isCanonicalExperienceUri("viking://user/test/memories/experiences/a.md")).toBe(true);
    expect(isCanonicalExperienceUri("viking://user/test/memories/experiences/.overview.md")).toBe(false);
    expect(isCanonicalExperienceUri("viking://user/test/memories/experiences/a.md?version=1")).toBe(false);
    expect(isCanonicalExperienceUri("viking://user/test/memories/experiences/../a.md")).toBe(false);
  });
});
