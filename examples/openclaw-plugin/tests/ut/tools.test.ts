import { describe, expect, it, vi } from "vitest";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import contextEnginePlugin from "../../index.js";
import {
  parseAddResourceCommandArgs,
  parseAddSkillCommandArgs,
  parseOVSearchCommandArgs,
  tokenizeCommandArgs,
} from "../../plugin/openviking-command-args.js";
import type { FindResultItem } from "../../client.js";
import { openClawSessionToOvStorageId } from "../../routing/identity-routing.js";

type ToolDef = {
  name: string;
  description: string;
  parameters?: unknown;
  execute: (toolCallId: string, params: Record<string, unknown>) => Promise<unknown>;
};

type CommandDef = {
  name: string;
  description: string;
  acceptsArgs?: boolean;
  handler: (ctx: {
    args?: string;
    commandBody: string;
    sessionKey?: string;
    sessionId?: string;
    agentId?: string;
    ovSessionId?: string;
  }) => Promise<{ text: string }>;
};

type ToolResult = {
  content: Array<{ type: string; text: string }>;
  details: Record<string, unknown>;
};

function okResponse(result: unknown): Response {
  return new Response(JSON.stringify({ status: "ok", result }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function setupPlugin(
  clientOverrides?: Record<string, unknown>,
  pluginConfigOverrides?: Record<string, unknown>,
) {
  const tools = new Map<string, ToolDef>();
  const factoryTools = new Map<string, (ctx: Record<string, unknown>) => ToolDef>();
  const commands = new Map<string, CommandDef>();

  const mockClient = {
    find: vi.fn().mockResolvedValue({ memories: [], total: 0 }),
    read: vi.fn().mockResolvedValue("content"),
    addSessionMessage: vi.fn().mockResolvedValue(undefined),
    commitSession: vi.fn().mockResolvedValue({
      status: "completed",
      archived: false,
      memories_extracted: { core: 2 },
    }),
    deleteUri: vi.fn().mockResolvedValue(undefined),
    list: vi.fn().mockResolvedValue([]),
    getSessionArchive: vi.fn().mockResolvedValue({
      archive_id: "archive_001",
      abstract: "Test archive",
      overview: "",
      messages: [],
    }),
    healthCheck: vi.fn().mockResolvedValue(undefined),
    getSession: vi.fn().mockResolvedValue({ pending_tokens: 0 }),
    getSessionContext: vi.fn().mockResolvedValue({
      latest_archive_overview: "",
      latest_archive_id: "",
      pre_archive_abstracts: [],
      messages: [],
      estimatedTokens: 0,
      stats: { totalArchives: 0, includedArchives: 0, droppedArchives: 0, failedArchives: 0, activeTokens: 0, archiveTokens: 0 },
    }),
    ...clientOverrides,
  };

  const api = {
    pluginConfig: {
      mode: "remote",
      baseUrl: "http://127.0.0.1:1933",
      autoCapture: false,
      autoRecall: false,
      ...pluginConfigOverrides,
    },
    logger: {
      info: vi.fn(),
      warn: vi.fn(),
      error: vi.fn(),
      debug: vi.fn(),
    },
    registerTool: vi.fn((toolOrFactory: unknown, opts?: unknown) => {
      if (typeof toolOrFactory === "function") {
        const factory = toolOrFactory as (ctx: Record<string, unknown>) => ToolDef;
        const tool = factory({ sessionId: "test-session" });
        factoryTools.set(tool.name, factory);
        tools.set(tool.name, tool);
      } else {
        const tool = toolOrFactory as ToolDef;
        tools.set(tool.name, tool);
      }
    }),
    registerCommand: vi.fn((command: unknown) => {
      const cmd = command as CommandDef;
      commands.set(cmd.name, cmd);
    }),
    registerHttpRoute: vi.fn(),
    registerService: vi.fn(),
    registerContextEngine: vi.fn(),
    on: vi.fn(),
  };

  return { tools, factoryTools, commands, mockClient, api };
}

function makeMemory(overrides?: Partial<FindResultItem>): FindResultItem {
  return {
    uri: "viking://user/default/memories/m1",
    level: 2,
    abstract: "User prefers Python for backend",
    category: "preferences",
    score: 0.85,
    ...overrides,
  };
}

// Since the tools are closures that capture the client from register(),
// we test the pure logic aspects and use the index.ts exports for the rest.

describe("Tool: memory_recall (registration)", () => {
  it("registers with correct name and description", () => {
    const { tools, api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    const recall = tools.get("memory_recall");
    expect(recall).toBeDefined();
    expect(recall!.name).toBe("memory_recall");
    expect(recall!.description).toContain("Search long-term memories");
  });

  it("registers with query, limit, scoreThreshold, targetUri parameters", () => {
    const { tools, api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    const recall = tools.get("memory_recall");
    expect(recall).toBeDefined();
    const schema = recall!.parameters as Record<string, unknown>;
    const props = (schema as any).properties;
    expect(props).toHaveProperty("query");
    expect(props).toHaveProperty("limit");
    expect(props).toHaveProperty("scoreThreshold");
    expect(props).toHaveProperty("targetUri");
  });

  it("fills L2 content and filters explicit recall results like auto-recall", async () => {
    const openVikingTransport = vi.fn(async (url: string, init?: RequestInit) => {
      const requestUrl = new URL(url);
      if (requestUrl.pathname === "/api/v1/system/status") {
        return okResponse({ user: "default" });
      }

      if (requestUrl.pathname === "/api/v1/search/find") {
        const body = JSON.parse(String(init?.body ?? "{}"));
        const contextType = String(body.context_type ?? "");
        const memories =
          contextType === "memory"
            ? [
                makeMemory({
                  uri: "viking://user/default/memories/high",
                  abstract: "Abstract only text",
                  score: 0.92,
                }),
                makeMemory({
                  uri: "viking://user/default/memories/low",
                  abstract: "Low score text",
                  score: 0.05,
                }),
              ]
            : [];
        return okResponse({ memories, total: memories.length });
      }

      if (requestUrl.pathname === "/api/v1/content/read") {
        expect(requestUrl.searchParams.get("uri")).toBe("viking://user/default/memories/high");
        return okResponse("Full L2 content from read");
      }

      return okResponse({});
    });

    const { factoryTools, api } = setupPlugin(undefined, {
      recallLimit: 1,
      recallPreferAbstract: true,
      recallScoreThreshold: 0.2,
      recallTargetTypes: ["user", "agent"],
    });
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    const factory = factoryTools.get("memory_recall");
    expect(factory).toBeDefined();

    const tool = factory!({ sessionId: "test-session", agentId: "main" });
    const result = await tool.execute("tc-memory-recall", {
      query: "backend preference",
      limit: 1,
      scoreThreshold: 0.2,
    }) as ToolResult;

    expect(result.content[0]!.text).toContain("Full L2 content from read");
    expect(result.content[0]!.text).not.toContain("Abstract only text");
    expect(result.content[0]!.text).not.toContain("Low score text");

    const findCalls = openVikingTransport.mock.calls.filter(([calledUrl]) =>
      String(calledUrl).includes("/api/v1/search/find")
    );
    expect(findCalls).toHaveLength(1);
    for (const [, init] of findCalls) {
      const body = JSON.parse(String((init as RequestInit).body));
      expect(body.limit).toBe(20);
      expect(body.score_threshold).toBe(0);
      expect(body.context_type).toBe("memory");
      expect(body.target_uri).toBeUndefined();
    }
  });

  it("applies recallMaxInjectedChars to explicit memory_recall output", async () => {
    const openVikingTransport = vi.fn(async (url: string, init?: RequestInit) => {
      const requestUrl = new URL(url);
      if (requestUrl.pathname === "/api/v1/system/status") {
        return okResponse({ user: "default" });
      }

      if (requestUrl.pathname === "/api/v1/search/find") {
        const body = JSON.parse(String(init?.body ?? "{}"));
        const contextType = String(body.context_type ?? "");
        const memories =
          contextType === "memory"
            ? [
                makeMemory({
                  uri: "viking://user/default/memories/large",
                  abstract: "Large abstract",
                  score: 0.95,
                }),
                makeMemory({
                  uri: "viking://user/default/memories/small",
                  abstract: "Small abstract",
                  score: 0.9,
                }),
              ]
            : [];
        return okResponse({ memories, total: memories.length });
      }

      if (requestUrl.pathname === "/api/v1/content/read") {
        const uri = requestUrl.searchParams.get("uri");
        return okResponse(uri?.endsWith("/large") ? "x".repeat(200) : "short");
      }

      return okResponse({});
    });

    const { factoryTools, api } = setupPlugin(undefined, {
      recallLimit: 2,
      recallMaxInjectedChars: 20,
      recallScoreThreshold: 0.2,
      recallTargetTypes: ["user", "agent"],
    });
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    const factory = factoryTools.get("memory_recall");
    expect(factory).toBeDefined();

    const tool = factory!({ sessionId: "test-session", agentId: "main" });
    const result = await tool.execute("tc-memory-recall-budget", {
      query: "backend preference",
      limit: 2,
      scoreThreshold: 0.2,
    }) as ToolResult;

    expect(result.content[0]!.text).toContain("Found 1 memories");
    expect(result.content[0]!.text).toContain("- [preferences] short");
    expect(result.content[0]!.text).not.toContain("x".repeat(200));
    expect(result.details.count).toBe(1);
  });

  it("applies /ov-query-config session settings to subsequent memory_recall", async () => {
    const openVikingTransport = vi.fn(async (url: string, init?: RequestInit) => {
      const requestUrl = new URL(url);
      if (requestUrl.pathname === "/api/v1/system/status") {
        return okResponse({ user: "default" });
      }

      if (requestUrl.pathname === "/api/v1/search/find") {
        const body = JSON.parse(String(init?.body ?? "{}"));
        const contextType = String(body.context_type ?? "");
        const memories = contextType === "memory"
          ? [
              makeMemory({
                uri: "viking://user/default/memories/high",
                abstract: "High score runtime memory",
                score: 0.92,
              }),
              makeMemory({
                uri: "viking://user/default/memories/low",
                abstract: "Low score runtime memory",
                score: 0.1,
              }),
            ]
          : [];
        return okResponse({ memories, total: memories.length });
      }

      if (requestUrl.pathname === "/api/v1/content/read") {
        return okResponse("High score runtime memory content");
      }

      return okResponse({});
    });

    const { factoryTools, commands, api } = setupPlugin(undefined, {
      recallLimit: 6,
      recallTargetTypes: ["user", "agent"],
    });
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    const command = commands.get("ov-query-config");
    expect(command).toBeDefined();

    await command!.handler({
      args: "set --scope session --recallLimit 1 --candidateLimit 3 --scoreThreshold 0.5 --resourceTypes user",
      commandBody: "",
      sessionId: "runtime-session",
      agentId: "main",
    });

    const tool = factoryTools.get("memory_recall")!({ sessionId: "runtime-session", agentId: "main" });
    const result = await tool.execute("tc-memory-recall-runtime", {
      query: "runtime memory",
    }) as ToolResult;

    expect(result.content[0]!.text).toContain("High score runtime memory content");
    expect(result.content[0]!.text).not.toContain("Low score runtime memory");
    const findCalls = openVikingTransport.mock.calls.filter(([calledUrl]) =>
      String(calledUrl).includes("/api/v1/search/find")
    );
    expect(findCalls).toHaveLength(1);
    const body = JSON.parse(String((findCalls[0]![1] as RequestInit).body));
    expect(body.context_type).toBe("memory");
    expect(body.target_uri).toBeUndefined();
    expect(body.limit).toBe(3);
  });

  it("supports /ov-query-config get, unset, and reset for session scope", async () => {
    const { commands, api } = setupPlugin(undefined, {
      recallLimit: 6,
      recallScoreThreshold: 0.15,
    });
    contextEnginePlugin.register(api as any);
    const command = commands.get("ov-query-config");
    expect(command).toBeDefined();

    const ctx = {
      commandBody: "",
      sessionId: "runtime-session-config",
      agentId: "main",
    };

    const setResult = await command!.handler({
      ...ctx,
      args: "set --scope session --recallLimit 2 --scoreThreshold 0.4",
    });
    expect(setResult.text).toContain("Updated OpenViking query config");
    expect((setResult.details?.effective as any).recallLimit).toBe(2);
    expect((setResult.details?.effective as any).scoreThreshold).toBe(0.4);

    const getResult = await command!.handler({ ...ctx, args: "get --scope session" });
    expect((getResult.details?.effective as any).recallLimit).toBe(2);
    expect((getResult.details?.effective as any).scoreThreshold).toBe(0.4);

    const unsetResult = await command!.handler({ ...ctx, args: "unset recallLimit --scope session" });
    expect(unsetResult.text).toContain("Unset OpenViking query config fields");
    expect((unsetResult.details?.effective as any).recallLimit).toBe(6);
    expect((unsetResult.details?.effective as any).scoreThreshold).toBe(0.4);

    const resetResult = await command!.handler({ ...ctx, args: "reset --scope session" });
    expect(resetResult.text).toContain("Reset OpenViking query config");
    expect((resetResult.details?.effective as any).recallLimit).toBe(6);
    expect((resetResult.details?.effective as any).scoreThreshold).toBe(0.15);
  });

  it("supports /ov-query-config weight parameters and recallPreferAbstract", async () => {
    const { commands, api } = setupPlugin(undefined, {
      recallPreferAbstract: true,
    });
    contextEnginePlugin.register(api as any);
    const command = commands.get("ov-query-config");
    expect(command).toBeDefined();

    const result = await command!.handler({
      args: "set --scope session --weight baseScore=0.5,leaf=0.7 --categoryWeight preferences=1.5 --resourceTypeWeight user=0.25 --recallPreferAbstract false",
      commandBody: "",
      sessionId: "runtime-weight-session",
      agentId: "main",
    });

    const effective = result.details?.effective as any;
    expect(effective.rankingWeights).toMatchObject({ baseScore: 0.5, leaf: 0.7 });
    expect(effective.categoryWeights).toMatchObject({ preferences: 1.5 });
    expect(effective.resourceTypeWeights).toMatchObject({ user: 0.25 });
    expect(effective.recallPreferAbstract).toBe(false);
    expect(result.details?.params).toMatchObject({
      rankingWeights: { baseScore: 0.5, leaf: 0.7 },
      categoryWeights: { preferences: 1.5 },
      resourceTypeWeights: { user: 0.25 },
      recallPreferAbstract: false,
    });
  });

  it("rejects empty /ov-query-config set patches without overwriting existing config", async () => {
    const { commands, api } = setupPlugin(undefined, { recallLimit: 6 });
    contextEnginePlugin.register(api as any);
    const command = commands.get("ov-query-config");
    expect(command).toBeDefined();
    const ctx = {
      commandBody: "",
      sessionId: "runtime-empty-patch-session",
      agentId: "main",
    };

    await command!.handler({ ...ctx, args: "set --scope session --recallLimit 2" });
    const emptyResult = await command!.handler({ ...ctx, args: "set --scope session --unknown 1" });
    const getResult = await command!.handler({ ...ctx, args: "get --scope session" });

    expect(emptyResult.text).toContain("No query config parameters provided");
    expect((getResult.details?.effective as any).recallLimit).toBe(2);
  });
});

describe("Tool: memory_store (behavioral)", () => {
  it("registers with correct name and description", () => {
    const { tools, api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    const store = tools.get("memory_store");
    expect(store).toBeDefined();
    expect(store!.name).toBe("memory_store");
    expect(store!.description).toContain("Store text");
    expect(store!.description).toContain("explicitly asks to remember");
    expect(store!.description).toContain("threshold/commit dependent");
  });

  it("uses requesterSenderId to populate peer_id for user writes", async () => {
    const openVikingTransport = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/api/v1/system/status")) {
        return okResponse({ user: "default" });
      }
      if (url.includes("/messages")) {
        return okResponse({ session_id: "sess-1" });
      }
      if (url.endsWith("/commit")) {
        return okResponse({
          status: "completed",
          archived: false,
          memories_extracted: { core: 1 },
        });
      }
      return okResponse({});
    });

    const { factoryTools, api } = setupPlugin();
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    const factory = factoryTools.get("memory_store");
    expect(factory).toBeDefined();

    const tool = factory!({
      sessionId: "runtime-session",
      sessionKey: "agent:main:main",
      requesterSenderId: "wx/user-01@abc",
    });

    await tool.execute("tc-memory-store", { text: "hello from tool" });

    const messageCall = openVikingTransport.mock.calls.find(([url]) =>
      String(url).includes("/api/v1/sessions/") && String(url).includes("/messages"),
    );
    expect(messageCall).toBeDefined();
    const [, init] = messageCall as [string, RequestInit];
    const body = JSON.parse(String(init.body));
    expect(body.role).toBe("user");
    expect(body.peer_id).toBe("wx_user-01_abc");
    expect(body).not.toHaveProperty("role_id");
  });

  it("uses a temporary session by default instead of the current tool session", async () => {
    const openVikingTransport = vi.fn(async (url: string) => {
      if (url.endsWith("/api/v1/system/status")) {
        return okResponse({ user: "default" });
      }
      if (url.includes("/messages")) {
        return okResponse({ session_id: "sess-1" });
      }
      if (url.endsWith("/commit")) {
        return okResponse({ status: "completed", archived: false, memories_extracted: {} });
      }
      return okResponse({});
    });

    const { factoryTools, api } = setupPlugin();
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    const tool = factoryTools.get("memory_store")!({
      sessionId: "runtime-session",
      sessionKey: "agent:main:main",
    });

    await tool.execute("tc-memory-store", { text: "hello from tool" });

    const messageCall = openVikingTransport.mock.calls.find(([url]) =>
      String(url).includes("/api/v1/sessions/") && String(url).includes("/messages"),
    );
    expect(String(messageCall?.[0])).toContain("/api/v1/sessions/memory-store-");
  });

  it("normalizes explicit memory_store sessionId without using current sessionKey", async () => {
    const openVikingTransport = vi.fn(async (url: string) => {
      if (url.endsWith("/api/v1/system/status")) {
        return okResponse({ user: "default" });
      }
      if (url.includes("/messages")) {
        return okResponse({ session_id: "sess-1" });
      }
      if (url.endsWith("/commit")) {
        return okResponse({ status: "completed", archived: false, memories_extracted: {} });
      }
      return okResponse({});
    });

    const { factoryTools, api } = setupPlugin();
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    const tool = factoryTools.get("memory_store")!({
      sessionId: "runtime-session",
      sessionKey: "agent:main:main",
    });

    await tool.execute("tc-memory-store", {
      text: "hello from tool",
      sessionId: "C:\\Users\\test",
    });

    const messageCall = openVikingTransport.mock.calls.find(([url]) =>
      String(url).includes("/api/v1/sessions/") && String(url).includes("/messages"),
    );
    expect(String(messageCall?.[0])).not.toContain("runtime-session");
    expect(String(messageCall?.[0])).not.toContain("agent%3Amain%3Amain");
    expect(String(messageCall?.[0])).toMatch(/\/api\/v1\/sessions\/[a-f0-9]{64}\/messages$/);
  });
});

describe("Tool: memory_forget (behavioral)", () => {
  it("registers with correct name and description", () => {
    const { tools, api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    const forget = tools.get("memory_forget");
    expect(forget).toBeDefined();
    expect(forget!.name).toBe("memory_forget");
    expect(forget!.description).toContain("Forget memory");
  });
});

describe("Tool: ov_archive_expand (behavioral)", () => {
  it("registers as factory tool with correct name", () => {
    const { factoryTools, api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    const factory = factoryTools.get("ov_archive_expand");
    expect(factory).toBeDefined();
    const tool = factory!({ sessionId: "test-session", sessionKey: "sk" });
    expect(tool.name).toBe("ov_archive_expand");
    expect(tool.description).toContain("archive");
  });

  it("factory-created tool returns error when archiveId is empty", async () => {
    const { factoryTools, api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    const factory = factoryTools.get("ov_archive_expand");
    const tool = factory!({ sessionId: "test-session" });

    const result = await tool.execute("tc1", { archiveId: "" }) as ToolResult;
    expect(result.content[0]!.text).toContain("archiveId is required");
    expect(result.details.error).toBe("missing_param");
  });

  it("factory-created tool returns error when sessionId is missing", async () => {
    const { factoryTools, api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    const factory = factoryTools.get("ov_archive_expand");
    const tool = factory!({});

    const result = await tool.execute("tc2", { archiveId: "archive_001" }) as ToolResult;
    expect(result.content[0]!.text).toContain("no active session");
    expect(result.details.error).toBe("no_session");
  });
});

describe("Tool: OpenViking tool result access", () => {
  it("registers read, search, and list tools", () => {
    const { tools, api } = setupPlugin();
    contextEnginePlugin.register(api as any);

    expect(tools.get("openviking_tool_result_read")).toBeDefined();
    expect(tools.get("openviking_tool_result_search")).toBeDefined();
    expect(tools.get("openviking_tool_result_list")).toBeDefined();
  });

  it("reads an externalized tool result chunk for the current session", async () => {
    const openVikingTransport = vi.fn(async (url: string) => {
      expect(url).toContain("/api/v1/sessions/test-session/tool-results/tr_call_abc");
      expect(url).toContain("offset=5");
      expect(url).toContain("limit=10");
      expect(url).toContain("include_metadata=true");
      return okResponse({
        tool_result_id: "tr_call_abc",
        content: "raw",
        offset: 5,
        limit: 10,
        offset_unit: "unicode_code_point",
        total_chars: 42,
        has_more: true,
        metadata: {
          storage_uri: "viking://session/test-session/tool-results/tr_call_abc",
          tool_name: "read_file",
        },
      });
    });

    const { tools, api } = setupPlugin();
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    const tool = tools.get("openviking_tool_result_read")!;

    const result = await tool.execute("tc-read", {
      tool_output_ref: "viking://session/test-session/tool-results/tr_call_abc",
      offset: 5,
      limit: 10,
    }) as ToolResult;

    expect(result.content[0]!.text).toBe("raw");
    expect(result.details).toMatchObject({
      action: "read",
      tool_output_ref: "viking://session/test-session/tool-results/tr_call_abc",
      tool_result_id: "tr_call_abc",
      offset: 5,
      limit: 10,
      returned_chars: 3,
      total_chars: 42,
      has_more: true,
      next_offset: 8,
    });
  });

  it("searches within an externalized tool result", async () => {
    const openVikingTransport = vi.fn(async (url: string) => {
      expect(url).toContain("/api/v1/sessions/test-session/tool-results/tr_call_abc/search?");
      expect(url).toContain("q=needle");
      expect(url).toContain("limit=2");
      expect(url).toContain("context_chars=15");
      return okResponse({
        tool_result_id: "tr_call_abc",
        matches: [
          {
            offset: 12,
            offset_unit: "unicode_code_point",
            snippet: "hay needle stack",
          },
        ],
      });
    });

    const { tools, api } = setupPlugin();
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    const tool = tools.get("openviking_tool_result_search")!;

    const result = await tool.execute("tc-search", {
      tool_output_ref: "viking://session/test-session/tool-results/tr_call_abc",
      query: "needle",
      limit: 2,
      context_chars: 15,
    }) as ToolResult;

    expect(result.content[0]!.text).toContain("Found 1 match");
    expect(result.content[0]!.text).toContain("offset 12");
    expect(result.content[0]!.text).toContain("hay needle stack");
    expect(result.details).toMatchObject({
      action: "searched",
      tool_output_ref: "viking://session/test-session/tool-results/tr_call_abc",
      tool_result_id: "tr_call_abc",
      query: "needle",
      match_count: 1,
    });
  });

  it("lists externalized tool results for the current session", async () => {
    const openVikingTransport = vi.fn(async (url: string) => {
      expect(url).toContain("/api/v1/sessions/test-session/tool-results?");
      expect(url).toContain("tool_name=read_file");
      expect(url).toContain("limit=5");
      return okResponse({
        tool_results: [
          {
            tool_result_id: "tr_call_abc",
            storage_uri: "viking://session/test-session/tool-results/tr_call_abc",
            tool_name: "read_file",
            original_chars: 42000,
            created_at: "2026-05-15T00:00:00Z",
          },
        ],
      });
    });

    const { tools, api } = setupPlugin();
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    const tool = tools.get("openviking_tool_result_list")!;

    const result = await tool.execute("tc-list", {
      tool_name: "read_file",
      limit: 5,
    }) as ToolResult;

    expect(result.content[0]!.text).toContain("read_file");
    expect(result.content[0]!.text).toContain("original_chars=42000");
    expect(result.content[0]!.text).toContain("viking://session/test-session/tool-results/tr_call_abc");
    expect(result.details).toMatchObject({
      action: "listed",
      session_id: "test-session",
      tool_name: "read_file",
      count: 1,
    });
  });

  it("rejects refs from another session", async () => {
    const { tools, api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    const tool = tools.get("openviking_tool_result_read")!;

    const result = await tool.execute("tc-read", {
      tool_output_ref: "viking://session/other-session/tool-results/tr_call_abc",
    }) as ToolResult;

    expect(result.content[0]!.text).toContain("another session");
    expect(result.details.error).toBe("session_mismatch");
  });
});

describe("Tool: add_resource, add_skill, and ov_search (registration)", () => {
  it("does not register add_resource tool by default", () => {
    const { tools, api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    expect(tools.get("add_resource")).toBeUndefined();
  });

  it("registers add_resource tool with expected parameters only when explicitly enabled", () => {
    const { tools, api } = setupPlugin(undefined, { enableAddResourceTool: true });
    contextEnginePlugin.register(api as any);
    const tool = tools.get("add_resource");
    expect(tool).toBeDefined();
    expect(tool!.description).toContain("explicitly asks");
    expect(tool!.description).toContain("[media attached: /path");
    expect(tool!.description).toContain("Do not invent OpenViking upload REST endpoints");
    const props = (tool!.parameters as any).properties;
    expect(props).toHaveProperty("source");
    expect(props.source.description).toContain("OpenClaw media attachment path");
    expect(props).toHaveProperty("to");
    expect(props).toHaveProperty("parent");
    expect(props).toHaveProperty("reason");
    expect(props).toHaveProperty("instruction");
    expect(props).toHaveProperty("wait");
  });

  it("registers add_skill tool with expected parameters", () => {
    const { tools, api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    const tool = tools.get("add_skill");
    expect(tool).toBeDefined();
    expect(tool!.description).toContain("explicitly asks");
    expect(tool!.description).toContain("into OpenViking");
    expect(tool!.description).toContain("SKILL.md");
    expect(tool!.description).toContain("MCP tool dict");
    const props = (tool!.parameters as any).properties;
    expect(props).toHaveProperty("source");
    expect(props).toHaveProperty("data");
    expect(props).toHaveProperty("wait");
    expect(props).toHaveProperty("timeout");
    expect(props).not.toHaveProperty("to");
    expect(props).not.toHaveProperty("parent");
  });

  it("registers ov_search tool with natural-language trigger guidance", () => {
    const { tools, api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    const tool = tools.get("ov_search");
    expect(tool).toBeDefined();
    // Avoid colliding with OpenClaw's built-in memory_search tool.
    expect(tools.get("memory_search")).toBeUndefined();
    expect(tool!.description).toContain("Search OpenViking resources and skills");
    expect(tool!.description).toContain("Use after importing");
    expect(tool!.description).toContain("call ov_read");
    expect(tool!.description).toContain("call ov_list on the parent URI");
    const props = (tool!.parameters as any).properties;
    expect(props).toHaveProperty("query");
    expect(props).toHaveProperty("uri");
    expect(props).toHaveProperty("limit");
  });

  it("registers ov_read and ov_multi_read tools with OpenViking URI guidance", () => {
    const { tools, api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    const tool = tools.get("ov_read");
    expect(tool).toBeDefined();
    expect(tool!.description).toContain("viking:// URI");
    expect(tool!.description).toContain("not local file paths");
    const props = (tool!.parameters as any).properties;
    expect(props).toHaveProperty("uri");
    expect(props.uri.description).toContain("Exact viking:// URI");

    const multiRead = tools.get("ov_multi_read");
    expect(multiRead).toBeDefined();
    expect(multiRead!.description).toContain("multiple exact OpenViking URIs");
    expect(multiRead!.description).toContain("sibling chunks");
    expect((multiRead!.parameters as any).properties).toHaveProperty("uris");
  });

  it("registers ov_list tool with directory browsing guidance", () => {
    const { tools, api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    const tool = tools.get("ov_list");
    expect(tool).toBeDefined();
    expect(tool!.description).toContain("List files and directories");
    expect(tool!.description).toContain("after ov_search");
    expect(tool!.description).toContain("sibling chunks");
    const props = (tool!.parameters as any).properties;
    expect(props).toHaveProperty("uri");
    expect(props).toHaveProperty("recursive");
    expect(props).toHaveProperty("simple");
    expect(props).toHaveProperty("limit");
  });
});

describe("Tool: ov_search (behavioral)", () => {
  it("searches resources and skills by default when no uri is provided", async () => {
    const openVikingTransport = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/api/v1/system/status")) {
        return okResponse({ user: "default" });
      }
      if (url.includes("/api/v1/fs/ls")) {
        return okResponse([]);
      }
      if (url.endsWith("/api/v1/search/find")) {
        const body = JSON.parse(String(init?.body ?? "{}"));
        if (body.target_uri === "viking://resources") {
          return okResponse({
            memories: [],
            resources: [
              {
                context_type: "resource",
                uri: "viking://resources/openviking-readme/README.md",
                level: 2,
                score: 0.82,
                category: "",
                match_reason: "",
                relations: [],
                abstract: "OpenViking install guide",
                overview: null,
              },
            ],
            skills: [],
            total: 1,
          });
        }
        return okResponse({
          memories: [],
          resources: [],
          skills: [
            {
              context_type: "skill",
              uri: "viking://user/skills/install-openviking-memory",
              level: 0,
              score: 0.7,
              category: "",
              match_reason: "",
              relations: [],
              abstract: "Install OpenViking memory integration",
              overview: null,
            },
          ],
          total: 1,
        });
      }
      return okResponse({});
    });

    const { tools, api } = setupPlugin();
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    const search = tools.get("ov_search")!;
    const result = await search.execute("tc1", { query: "OpenViking install" }) as ToolResult;

    expect(result.content[0]!.text).toContain("no");
    expect(result.content[0]!.text).toContain("type");
    expect(result.content[0]!.text).toContain("resource");
    expect(result.content[0]!.text).toContain("skill");
    expect(result.content[0]!.text).toContain("not local file paths");
    expect(result.content[0]!.text).toContain("ov_read");
    expect(result.content[0]!.text).toContain("Use ov_list on a hit's parent URI");
    expect(result.details.resources).toHaveLength(1);
    expect(result.details.skills).toHaveLength(1);

    const findBodies = openVikingTransport.mock.calls
      .filter((call) => String(call[0]).endsWith("/api/v1/search/find"))
      .map((call) => JSON.parse(String((call[1] as RequestInit).body)));
    expect(findBodies.some((body) => body.target_uri === "viking://resources")).toBe(true);
    expect(findBodies.some((body) => String(body.target_uri).startsWith("viking://user/") && String(body.target_uri).endsWith("/skills"))).toBe(true);
  });

  it("returns partial results when one default scope search fails", async () => {
    const openVikingTransport = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/api/v1/system/status")) {
        return okResponse({ user: "default" });
      }
      if (url.includes("/api/v1/fs/ls")) {
        return okResponse([]);
      }
      if (url.endsWith("/api/v1/search/find")) {
        const body = JSON.parse(String(init?.body ?? "{}"));
        if (body.target_uri === "viking://resources") {
          return okResponse({
            memories: [],
            resources: [
              {
                context_type: "resource",
                uri: "viking://resources/openviking-readme/README.md",
                level: 2,
                score: 0.82,
                category: "",
                match_reason: "",
                relations: [],
                abstract: "OpenViking install guide",
                overview: null,
              },
            ],
            skills: [],
            total: 1,
          });
        }
        throw new Error("skills search unavailable");
      }
      return okResponse({});
    });

    const { tools, api } = setupPlugin();
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    const search = tools.get("ov_search")!;
    const result = await search.execute("tc1", { query: "OpenViking install" }) as ToolResult;

    expect(result.details.resources).toHaveLength(1);
    expect(result.details.skills).toHaveLength(0);
    expect(result.content[0]!.text).toContain("resource");
  });

  it("renders memory hits when explicit uri returns memories", async () => {
    const openVikingTransport = vi.fn(async (url: string) => {
      if (url.endsWith("/api/v1/search/find")) {
        return okResponse({
          memories: [
            {
              context_type: "memory",
              uri: "viking://user/default/memories/preferences/theme.md",
              level: 2,
              score: 0.91,
              category: "preferences",
              match_reason: "",
              relations: [],
              abstract: "User prefers dark theme",
              overview: null,
            },
          ],
          resources: [],
          skills: [],
          total: 1,
        });
      }
      return okResponse({});
    });

    const { tools, api } = setupPlugin();
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    const search = tools.get("ov_search")!;
    const result = await search.execute("tc1", {
      query: "theme",
      uri: "viking://user/default/memories",
    }) as ToolResult;

    expect(result.details.memories).toHaveLength(1);
    expect(result.content[0]!.text).toContain("memory");
    expect(result.content[0]!.text).toContain("User prefers dark theme");
  });

  it("reads an OpenViking URI through ov_read instead of filesystem semantics", async () => {
    const openVikingTransport = vi.fn(async (url: string) => {
      if (url.includes("/api/v1/content/read")) {
        expect(url).toContain(encodeURIComponent("viking://resources/openviking-readme/README.md#chunk-1"));
        return okResponse("# README\nOpenViking install guide");
      }
      return okResponse({});
    });

    const { tools, api } = setupPlugin();
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    const read = tools.get("ov_read")!;
    const result = await read.execute("tc1", {
      uri: "viking://resources/openviking-readme/README.md#chunk-1",
    }) as ToolResult;

    expect(result.content[0]!.text).toContain("--- START OF viking://resources/openviking-readme/README.md#chunk-1 ---");
    expect(result.content[0]!.text).toContain("# README");
    expect(result.content[0]!.text).toContain("--- END OF viking://resources/openviking-readme/README.md#chunk-1 ---");
    expect(result.details.action).toBe("read");
    expect(result.details.uri).toBe("viking://resources/openviking-readme/README.md#chunk-1");
  });

  it("reads multiple OpenViking URIs and preserves per-URI failures", async () => {
    const openVikingTransport = vi.fn(async (url: string) => {
      const requestUrl = new URL(url);
      if (requestUrl.pathname === "/api/v1/content/read") {
        const uri = requestUrl.searchParams.get("uri");
        if (uri === "viking://resources/guide/missing.md") {
          return new Response(JSON.stringify({ status: "error", message: "not found" }), {
            status: 404,
            headers: { "Content-Type": "application/json" },
          });
        }
        return okResponse(`content for ${uri}`);
      }
      return okResponse({});
    });

    const { tools, api } = setupPlugin();
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    const multiRead = tools.get("ov_multi_read")!;
    const result = await multiRead.execute("tc-ov-multi-read", {
      uris: [
        "viking://resources/guide/.overview.md",
        "viking://resources/guide/missing.md",
      ],
    }) as ToolResult;

    expect(result.content[0]!.text).toContain("Multi-read results for 2 OpenViking resources");
    expect(result.content[0]!.text).toContain("--- START OF viking://resources/guide/.overview.md ---");
    expect(result.content[0]!.text).toContain("content for viking://resources/guide/.overview.md");
    expect(result.content[0]!.text).toContain("--- START OF viking://resources/guide/missing.md ---");
    expect(result.content[0]!.text).toContain("ERROR:");
    expect(result.details).toMatchObject({
      action: "multi_read",
      count: 2,
      success_count: 1,
    });
  });

  it("lists an OpenViking directory through the fs ls endpoint", async () => {
    const openVikingTransport = vi.fn(async (url: string) => {
      const requestUrl = new URL(url);
      if (requestUrl.pathname === "/api/v1/fs/ls") {
        expect(requestUrl.searchParams.get("uri")).toBe("viking://resources/guide");
        expect(requestUrl.searchParams.get("recursive")).toBe("true");
        expect(requestUrl.searchParams.get("simple")).toBe("false");
        expect(requestUrl.searchParams.get("node_limit")).toBe("5");
        return okResponse([
          {
            name: ".overview.md",
            uri: "viking://resources/guide/.overview.md",
            isDir: false,
            abstract: "Guide overview",
          },
          {
            name: "step-2.md",
            uri: "viking://resources/guide/step-2.md",
            isDir: false,
            abstract: "Second step",
          },
        ]);
      }
      return okResponse({});
    });

    const { tools, api } = setupPlugin();
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    const list = tools.get("ov_list")!;
    const result = await list.execute("tc-ov-list", {
      uri: "viking://resources/guide",
      recursive: true,
      limit: 5,
    }) as ToolResult;

    expect(result.content[0]!.text).toContain("Listed 2 OpenViking entries");
    expect(result.content[0]!.text).toContain("viking://resources/guide/.overview.md");
    expect(result.content[0]!.text).toContain("Second step");
    expect(result.details).toMatchObject({
      action: "listed",
      uri: "viking://resources/guide",
      recursive: true,
      simple: false,
      count: 2,
    });
  });

  it("passes simple list mode through to OpenViking", async () => {
    const openVikingTransport = vi.fn(async (url: string) => {
      const requestUrl = new URL(url);
      if (requestUrl.pathname === "/api/v1/fs/ls") {
        expect(requestUrl.searchParams.get("simple")).toBe("true");
        return okResponse(["viking://resources/guide/step-1.md"]);
      }
      return okResponse({});
    });

    const { tools, api } = setupPlugin();
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    const list = tools.get("ov_list")!;
    const result = await list.execute("tc-ov-list-simple", {
      uri: "viking://resources/guide",
      simple: true,
    }) as ToolResult;

    expect(result.content[0]!.text).toContain("viking://resources/guide/step-1.md");
    expect(result.details.simple).toBe(true);
  });

  it("prints full URIs in ov_search rows so ov_read does not receive display-truncated paths", async () => {
    const longUri = "viking://resources/harness-paper/2._OpenCompass司南_面向大模型时代的罗盘全面开放与分布式的评测体系/2.3_解决思路.md";
    const openVikingTransport = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/api/v1/system/status")) {
        return okResponse({ user: "default" });
      }
      if (url.includes("/api/v1/fs/ls")) {
        return okResponse([]);
      }
      if (url.endsWith("/api/v1/search/find")) {
        const body = JSON.parse(String(init?.body ?? "{}"));
        if (body.target_uri === "viking://resources") {
          return okResponse({
            memories: [],
            resources: [
              {
                context_type: "resource",
                uri: longUri,
                level: 2,
                score: 0.92,
                category: "",
                match_reason: "",
                relations: [],
                abstract: "OpenCompass evaluation details",
                overview: null,
              },
            ],
            skills: [],
            total: 1,
          });
        }
        return okResponse({ memories: [], resources: [], skills: [], total: 0 });
      }
      return okResponse({});
    });

    const { tools, api } = setupPlugin();
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    const search = tools.get("ov_search")!;
    const result = await search.execute("tc-long-uri", { query: "OpenCompass" }) as ToolResult;

    expect(result.content[0]!.text).toContain(longUri);
    expect(result.content[0]!.text).not.toContain("viking://resources/harness-paper/2._OpenCompass司南_面向大模型时代的罗盘全面开放与分布式...");
  });

  it("uses runtime ovSearchLimit and targetUri as ov_search defaults", async () => {
    const openVikingTransport = vi.fn(async (url: string, init?: RequestInit) => {
      const requestUrl = new URL(url);
      if (requestUrl.pathname === "/api/v1/system/status") {
        return okResponse({ user: "default" });
      }
      if (requestUrl.pathname === "/api/v1/search/find") {
        return okResponse({
          memories: [],
          resources: [
            {
              context_type: "resource",
              uri: "viking://resources/runtime-default/doc.md",
              level: 2,
              score: 0.88,
              category: "",
              match_reason: "",
              relations: [],
              abstract: "Runtime default search result",
              overview: null,
            },
          ],
          skills: [],
          total: 1,
        });
      }
      return okResponse({});
    });

    const { factoryTools, commands, api } = setupPlugin();
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    const command = commands.get("ov-query-config");
    expect(command).toBeDefined();
    await command!.handler({
      args: "set --scope session --ovSearchLimit 3 --targetUri viking://resources/runtime-default",
      commandBody: "",
      sessionId: "runtime-search-session",
      agentId: "main",
    });

    const search = factoryTools.get("ov_search")!({ sessionId: "runtime-search-session", agentId: "main" });
    const result = await search.execute("tc-runtime-ov-search", {
      query: "runtime default",
    }) as ToolResult;

    expect(result.details.resources).toHaveLength(1);
    const findCalls = openVikingTransport.mock.calls.filter(([calledUrl]) =>
      String(calledUrl).includes("/api/v1/search/find")
    );
    expect(findCalls).toHaveLength(1);
    const body = JSON.parse(String((findCalls[0]![1] as RequestInit).body));
    expect(body.target_uri).toBe("viking://resources/runtime-default");
    expect(body.limit).toBe(3);
  });

  it("rejects display-truncated ov_read URIs before calling OpenViking", async () => {
    const readMock = vi.fn().mockResolvedValue("content");
    const { tools, api } = setupPlugin({ read: readMock });
    contextEnginePlugin.register(api as any);
    const read = tools.get("ov_read")!;

    await expect(read.execute("tc-truncated-uri", {
      uri: "viking://resources/harness-paper/2._OpenCompass司南_面向大模型时代的罗盘全面开放与分布式的评测体系/2.3_解决思...",
    })).rejects.toThrow("truncated display URI");
    expect(readMock).not.toHaveBeenCalled();
  });
});

describe("OpenViking import command parsing", () => {
  it("tokenizes quoted args", () => {
    expect(tokenizeCommandArgs(`./README.md --reason "project docs" --wait`)).toEqual([
      "./README.md",
      "--reason",
      "project docs",
      "--wait",
    ]);
  });

  it("preserves Windows path backslashes in slash-command args", () => {
    expect(
      parseAddSkillCommandArgs(String.raw`C:\Users\alice\skill-dir --wait`),
    ).toMatchObject({
      source: String.raw`C:\Users\alice\skill-dir`,
      wait: true,
    });
  });

  it("parses add-resource flags", () => {
    expect(
      parseAddResourceCommandArgs(
        `./README.md --to viking://resources/readme --reason "project docs" --instruction='summarize APIs' --wait`,
      ),
    ).toMatchObject({
      source: "./README.md",
      to: "viking://resources/readme",
      reason: "project docs",
      instruction: "summarize APIs",
      wait: true,
    });
  });

  it("keeps unquoted space-containing import sources intact", () => {
    expect(
      parseAddResourceCommandArgs(
        `My Docs/README.md --to viking://resources/readme`,
      ),
    ).toMatchObject({
      source: "My Docs/README.md",
      to: "viking://resources/readme",
    });
  });

  it("rejects resource import with both to and parent", () => {
    expect(() =>
      parseAddResourceCommandArgs("./README.md --to viking://resources/a --parent viking://resources"),
    ).toThrow("Cannot specify both");
  });

  it("parses add-skill flags", () => {
    expect(parseAddSkillCommandArgs("./skills/demo --wait --timeout=30")).toMatchObject({
      source: "./skills/demo",
      wait: true,
      timeout: 30,
    });
  });

  it("rejects resource-only flags for skill imports", () => {
    expect(() =>
      parseAddSkillCommandArgs("./skills/demo --to viking://resources/nope"),
    ).toThrow("resource-only");
  });
});

describe("OpenViking ov_search command parsing", () => {
  it("parses ov_search query and flags", () => {
    expect(parseOVSearchCommandArgs(`"OpenViking install" --uri viking://resources --limit=3`)).toMatchObject({
      query: "OpenViking install",
      uri: "viking://resources",
      limit: 3,
    });
  });

  it("keeps multi-word unquoted slash-command queries intact", () => {
    expect(parseOVSearchCommandArgs(`OpenViking install --uri viking://resources`)).toMatchObject({
      query: "OpenViking install",
      uri: "viking://resources",
    });
  });
});

describe("Tool: ov_recall_trace", () => {
  it("registers recall trace query tool and slash command", () => {
    const { tools, commands, api } = setupPlugin(undefined, { traceRecall: true });
    contextEnginePlugin.register(api as any);

    const tool = tools.get("ov_recall_trace");
    expect(tool).toBeDefined();
    expect(tool!.description).toContain("recall trace");
    const props = (tool!.parameters as any).properties;
    expect(props).toHaveProperty("traceId");
    expect(props).toHaveProperty("sessionId");
    expect(props).toHaveProperty("source");
    expect(props).toHaveProperty("resourceTypes");
    expect(props).toHaveProperty("includeContent");
    expect(props).toHaveProperty("limit");

    expect(commands.get("ov-recall-trace")).toMatchObject({
      acceptsArgs: true,
      description: expect.stringContaining("recall trace"),
    });
  });

  it("includes selected content only when includeContent is requested", async () => {
    const openVikingTransport = vi.fn(async (url: string, init?: RequestInit) => {
      const requestUrl = new URL(url);
      if (requestUrl.pathname === "/api/v1/search/find") {
        return okResponse({
          memories: [],
          resources: [makeMemory({
            uri: "viking://resources/project/spec.md",
            abstract: "Recall trace design spec",
            score: 0.88,
          })],
          skills: [],
          total: 1,
        });
      }
      if (requestUrl.pathname === "/api/v1/content/read") {
        expect(requestUrl.searchParams.get("uri")).toBe("viking://resources/project/spec.md");
        return okResponse("Full trace content with operational details");
      }
      return okResponse({});
    });
    const { tools, factoryTools, commands, api } = setupPlugin(undefined, { traceRecall: true });
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    await tools.get("ov_search")!.execute("tc-search", { query: "trace design", uri: "viking://resources" });

    const traceTool = factoryTools.get("ov_recall_trace")!({ sessionId: "test-session" });
    const result = await traceTool.execute("tc-trace", { source: "ov_search", includeContent: true }) as ToolResult;
    const entry = (result.details.entries as any[])[0];
    expect(entry.selected[0].contentPreview).toContain("Full trace content");

    await commands.get("ov-recall-trace")!.handler({
      args: "--source ov_search --include-content",
      commandBody: "",
      sessionId: "test-session",
    });
    expect(openVikingTransport.mock.calls.filter(([calledUrl]) => String(calledUrl).includes("/api/v1/content/read")).length).toBeGreaterThanOrEqual(2);
  });

  it("registers recall trace gateway route when a route adapter is available", async () => {
    const { api } = setupPlugin(undefined, { traceRecall: true });
    contextEnginePlugin.register(api as any);
    const service = (api.registerService as any).mock.calls[0][0];
    const registerRoute = vi.fn();

    await service.start({ registerRoute });

    expect(registerRoute).toHaveBeenCalledWith(expect.objectContaining({
      method: "GET",
      path: "/api/openviking/recall-traces",
    }));
    expect(registerRoute).toHaveBeenCalledWith(expect.objectContaining({
      method: "GET",
      path: "/api/openviking/recall-traces/:traceId",
    }));
    expect(registerRoute).toHaveBeenCalledWith(expect.objectContaining({
      method: "GET",
      path: "/api/openviking/uri-detail",
    }));
    expect(registerRoute).toHaveBeenCalledWith(expect.objectContaining({
      method: "GET",
      path: "/api/openviking/recall-traces/latest-ov-search-list",
    }));
  });

  it("registers recall trace HTTP routes through the OpenClaw plugin API", () => {
    const { api } = setupPlugin(undefined, { traceRecall: true });

    contextEnginePlugin.register(api as any);

    expect(api.registerHttpRoute).toHaveBeenCalledWith(expect.objectContaining({
      path: "/api/openviking/recall-traces",
      auth: "plugin",
      match: "exact",
    }));
    expect(api.registerHttpRoute).toHaveBeenCalledWith(expect.objectContaining({
      path: "/api/openviking/recall-traces",
      auth: "plugin",
      match: "prefix",
    }));
    expect(api.registerHttpRoute).toHaveBeenCalledWith(expect.objectContaining({
      path: "/api/openviking/uri-detail",
      auth: "plugin",
      match: "exact",
    }));
    expect(api.registerHttpRoute).toHaveBeenCalledWith(expect.objectContaining({
      path: "/api/openviking/recall-traces/latest-ov-search-list",
      auth: "plugin",
      match: "exact",
    }));
  });

  it("serves URI detail through the gateway route without rerunning search", async () => {
    const openVikingTransport = vi.fn(async (url: string) => {
      const requestUrl = new URL(url);
      if (requestUrl.pathname === "/api/v1/content/read") {
        expect(requestUrl.searchParams.get("uri")).toBe("viking://resources/project/spec.md");
        return okResponse("0123456789abcdefghijklmnopqrstuvwxyz");
      }
      if (requestUrl.pathname === "/api/v1/search/find") {
        throw new Error("uri detail must not call find");
      }
      return okResponse({});
    });

    const { api } = setupPlugin(undefined, { traceRecall: true });
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    const service = (api.registerService as any).mock.calls[0][0];
    const routes = new Map<string, any>();
    await service.start({ registerRoute: (route: any) => routes.set(route.path, route) });

    const response = await routes.get("/api/openviking/uri-detail")!.handler({
      query: {
        uri: "viking://resources/project/spec.md",
        includeContent: "true",
        offset: "10",
        contentLimit: "5",
        agentId: "main",
      },
    }) as { status: number; body: any };

    expect(response.status).toBe(200);
    expect(response.body.ok).toBe(true);
    expect(response.body.uriType).toBe("resource");
    expect(response.body.content.text).toBe("abcde");
    expect(response.body.content.hasMore).toBe(true);
    expect(openVikingTransport.mock.calls.filter(([calledUrl]) => String(calledUrl).includes("/api/v1/search/find"))).toHaveLength(0);
  });

  it("serves latest ov_search simplified list from trace data", async () => {
    const openVikingTransport = vi.fn(async (url: string, init?: RequestInit) => {
      const requestUrl = new URL(url);
      if (requestUrl.pathname === "/api/v1/search/find") {
        const body = JSON.parse(String(init?.body ?? "{}"));
        if (body.target_uri === "viking://resources") {
          return okResponse({
            memories: [],
            resources: [makeMemory({
              uri: "viking://resources/project/spec.md",
              abstract: "Project spec abstract",
              score: 0.91,
            })],
            skills: [],
            total: 1,
          });
        }
        return okResponse({
          memories: [],
          resources: [],
          skills: [makeMemory({
            uri: "viking://user/skills/debugger",
            abstract: "Debugger skill",
            score: 0.83,
          })],
          total: 1,
        });
      }
      return okResponse({});
    });

    const { factoryTools, api } = setupPlugin(undefined, { traceRecall: true });
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    const searchTool = factoryTools.get("ov_search")!({ sessionId: "route-session", agentId: "main" });
    await searchTool.execute("tc-route-search", { query: "project spec", limit: 3 });

    const service = (api.registerService as any).mock.calls[0][0];
    const routes = new Map<string, any>();
    await service.start({ registerRoute: (route: any) => routes.set(route.path, route) });

    const response = await routes.get("/api/openviking/recall-traces/latest-ov-search-list")!.handler({
      query: {
        sessionId: "route-session",
        limit: "5",
        includeSkills: "false",
      },
    }) as { status: number; body: any };

    expect(response.status).toBe(200);
    expect(response.body.ok).toBe(true);
    expect(response.body.trace.triggerQuery).toBe("project spec");
    expect(response.body.items).toHaveLength(1);
    expect(response.body.items[0]).toMatchObject({
      uri: "viking://resources/project/spec.md",
      abstractPreview: "Project spec abstract",
      resultType: "resource",
      source: "selected",
      targetUri: "viking://resources",
    });
    expect(response.body.items[0].detailUrl).toContain("/api/openviking/uri-detail?uri=viking%3A%2F%2Fresources%2Fproject%2Fspec.md");
    expect(response.body.items.some((item: any) => item.resultType === "skill")).toBe(false);
  });

  it("filters recall trace gateway route by sessionKey aliases", async () => {
    const openVikingTransport = vi.fn(async (url: string) => {
      const requestUrl = new URL(url);
      if (requestUrl.pathname === "/api/v1/search/find") {
        return okResponse({
          memories: [],
          resources: [makeMemory({
            uri: "viking://resources/project/session-key-spec.md",
            abstract: "Session key specific result",
            score: 0.9,
          })],
          skills: [],
          total: 1,
        });
      }
      return okResponse({});
    });

    const { factoryTools, api } = setupPlugin(undefined, { traceRecall: true });
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    const searchTool = factoryTools.get("ov_search")!({
      sessionId: "route-session-by-key",
      sessionKey: "agent:main:route-session-by-key",
      agentId: "main",
    });
    await searchTool.execute("tc-route-search", { query: "session key route", limit: 3 });

    const otherSearchTool = factoryTools.get("ov_search")!({
      sessionId: "route-session-other-key",
      sessionKey: "agent:main:route-session-other-key",
      agentId: "main",
    });
    await otherSearchTool.execute("tc-route-search-other", { query: "other route", limit: 3 });

    const service = (api.registerService as any).mock.calls[0][0];
    const routes = new Map<string, any>();
    await service.start({ registerRoute: (route: any) => routes.set(route.path, route) });

    const response = await routes.get("/api/openviking/recall-traces")!.handler({
      query: {
        sessionkey: "agent:main:route-session-by-key",
        turn: "all",
        limit: "5",
      },
    }) as { status: number; body: any };

    expect(response.status).toBe(200);
    expect(response.body.entries).toHaveLength(1);
    expect(response.body.entries[0]).toMatchObject({
      source: "ov_search",
      sessionKey: "agent:main:route-session-by-key",
    });
  });

  it("does not combine explicit trace sessionKey filter with derived ovSessionId", async () => {
    const openVikingTransport = vi.fn(async (url: string) => {
      const requestUrl = new URL(url);
      if (requestUrl.pathname === "/api/v1/search/find") {
        return okResponse({
          memories: [],
          resources: [makeMemory({
            uri: "viking://resources/project/session-key-history.md",
            abstract: "Session key history result",
            score: 0.9,
          })],
          skills: [],
          total: 1,
        });
      }
      return okResponse({});
    });

    const { factoryTools, api } = setupPlugin(undefined, { traceRecall: true });
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    const sessionKey = "agent:main:historic-session-key";
    const searchTool = factoryTools.get("ov_search")!({
      sessionId: "11111111-1111-4111-8111-111111111111",
      sessionKey,
      agentId: "main",
    });
    await searchTool.execute("tc-route-search", { query: "historical route", limit: 3 });

    const traceTool = factoryTools.get("ov_recall_trace")!({
      sessionKey,
      agentId: "main",
    });
    const result = await traceTool.execute("tc-trace", {
      turn: "all",
      sessionKey,
      limit: 5,
    }) as ToolResult;

    expect(result.details.count).toBe(1);
    expect((result.details.entries as any[])[0]).toMatchObject({
      sessionKey,
      ovSessionId: "11111111-1111-4111-8111-111111111111",
      trigger: { query: "historical route" },
    });
  });

  it("uses outer sessionKey as the default trace identity without derived ovSessionId", async () => {
    const openVikingTransport = vi.fn(async (url: string) => {
      const requestUrl = new URL(url);
      if (requestUrl.pathname === "/api/v1/search/find") {
        return okResponse({
          memories: [],
          resources: [makeMemory({
            uri: "viking://resources/project/web-session-history.md",
            abstract: "Web session history result",
            score: 0.9,
          })],
          skills: [],
          total: 1,
        });
      }
      return okResponse({});
    });

    const { factoryTools, api } = setupPlugin(undefined, { traceRecall: true });
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    const sessionKey = "agent:main:web-c6592a8e-5448-4622-9e7f-31a07447eee7";
    const searchTool = factoryTools.get("ov_search")!({
      sessionId: "22222222-2222-4222-8222-222222222222",
      sessionKey,
      agentId: "main",
    });
    await searchTool.execute("tc-route-search", { query: "web session route", limit: 3 });

    const traceTool = factoryTools.get("ov_recall_trace")!({
      sessionKey,
      agentId: "main",
    });
    const result = await traceTool.execute("tc-trace", {
      turn: "all",
      limit: 5,
    }) as ToolResult;

    expect(result.details.count).toBe(1);
    expect((result.details.entries as any[])[0]).toMatchObject({
      sessionKey,
      ovSessionId: "22222222-2222-4222-8222-222222222222",
      trigger: { query: "web session route" },
    });
  });

  it("falls back to derived ovSessionId for legacy traces missing sessionKey", async () => {
    const openVikingTransport = vi.fn(async (url: string) => {
      const requestUrl = new URL(url);
      if (requestUrl.pathname === "/api/v1/search/find") {
        return okResponse({
          memories: [],
          resources: [makeMemory({
            uri: "viking://resources/project/legacy-trace.md",
            abstract: "Legacy trace result",
            score: 0.9,
          })],
          skills: [],
          total: 1,
        });
      }
      return okResponse({});
    });

    const { factoryTools, api } = setupPlugin(undefined, { traceRecall: true });
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    const sessionKey = "agent:main:legacy-session-key-only";
    const ovSessionId = openClawSessionToOvStorageId(undefined, sessionKey);
    const searchTool = factoryTools.get("ov_search")!({
      ovSessionId,
      agentId: "main",
    });
    await searchTool.execute("tc-legacy-search", { query: "legacy route", limit: 3 });

    const traceTool = factoryTools.get("ov_recall_trace")!({
      sessionKey,
      agentId: "main",
    });
    const result = await traceTool.execute("tc-trace", {
      turn: "all",
      limit: 5,
    }) as ToolResult;

    expect(result.details.count).toBe(1);
    expect((result.details.entries as any[])[0]).toMatchObject({
      ovSessionId,
      trigger: { query: "legacy route" },
    });
    expect((result.details.entries as any[])[0].sessionKey).toBeUndefined();
  });

  it("queries recorded traces from memory without calling OpenViking find", async () => {
    const openVikingTransport = vi.fn(async (url: string, init?: RequestInit) => {
      const requestUrl = new URL(url);
      if (requestUrl.pathname === "/api/v1/search/find") {
        const body = JSON.parse(String(init?.body ?? "{}"));
        if (body.target_uri === "viking://resources") {
          return okResponse({
            memories: [],
            resources: [makeMemory({
              uri: "viking://resources/project/spec.md",
              abstract: "Recall trace design spec",
              score: 0.88,
            })],
            skills: [],
            total: 1,
          });
        }
        return okResponse({ memories: [], resources: [], skills: [], total: 0 });
      }
      return okResponse({});
    });

    const { tools, factoryTools, api } = setupPlugin(undefined, { traceRecall: true });
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);

    await tools.get("ov_search")!.execute("tc-search", {
      query: "trace design",
      uri: "viking://resources",
      limit: 3,
    });

    openVikingTransport.mockClear();
    const traceTool = factoryTools.get("ov_recall_trace")!({ sessionId: "test-session" });
    const result = await traceTool.execute("tc-trace", {
      source: "ov_search",
      limit: 10,
    }) as ToolResult;

    expect(result.content[0]!.text).toContain("ov_search");
    expect(result.content[0]!.text).toContain("trace design");
    expect(result.content[0]!.text).toContain("viking://resources/project/spec.md");
    expect(result.details.count).toBe(1);
    expect(openVikingTransport.mock.calls.some(([calledUrl]) => String(calledUrl).includes("/api/v1/search/find"))).toBe(false);
  });

  it("bounds stored trace query text by traceRecallQueryMaxChars", async () => {
    const openVikingTransport = vi.fn(async (url: string) => {
      const requestUrl = new URL(url);
      if (requestUrl.pathname === "/api/v1/search/find") {
        return okResponse({ memories: [], resources: [], skills: [], total: 0 });
      }
      return okResponse({});
    });

    const { tools, factoryTools, api } = setupPlugin(undefined, {
      traceRecall: true,
      traceRecallQueryMaxChars: 200,
    });
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);

    await tools.get("ov_search")!.execute("tc-search-long-query", {
      query: "q".repeat(500),
      uri: "viking://resources",
    });

    const trace = factoryTools.get("ov_recall_trace")!({ sessionId: "test-session" });
    const result = await trace.execute("tc-trace", { source: "ov_search", limit: 10 }) as ToolResult;
    const entry = (result.details.entries as any[])[0];

    expect(entry.trigger.query).toHaveLength(200);
    expect(entry.trigger.queryTruncated).toBe(true);
  });

  it("records explicit memory_recall traces with selected injected memories", async () => {
    const openVikingTransport = vi.fn(async (url: string, init?: RequestInit) => {
      const requestUrl = new URL(url);
      if (requestUrl.pathname === "/api/v1/system/status") {
        return okResponse({ user: "default" });
      }
      if (requestUrl.pathname === "/api/v1/search/find") {
        const body = JSON.parse(String(init?.body ?? "{}"));
        const memories = String(body.context_type ?? "") === "memory"
          ? [makeMemory({ uri: "viking://user/default/memories/high", abstract: "Backend preference", score: 0.91 })]
          : [];
        return okResponse({ memories, resources: [], skills: [], total: memories.length });
      }
      if (requestUrl.pathname === "/api/v1/content/read") {
        return okResponse("Full backend memory content");
      }
      return okResponse({});
    });

    const { factoryTools, api } = setupPlugin(undefined, {
      traceRecall: true,
      recallResources: false,
      recallTargetTypes: ["user"],
    });
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    const recall = factoryTools.get("memory_recall")!({ sessionId: "test-session", agentId: "main" });
    await recall.execute("tc-recall", { query: "backend preference", limit: 1, scoreThreshold: 0.2 });

    const trace = factoryTools.get("ov_recall_trace")!({ sessionId: "test-session" });
    const result = await trace.execute("tc-trace", { source: "memory_recall", limit: 10 }) as ToolResult;

    expect(result.content[0]!.text).toContain("memory_recall");
    expect(result.content[0]!.text).toContain("backend preference");
    expect(result.content[0]!.text).toContain("viking://user/default/memories/high");
    const entry = (result.details.entries as any[])[0];
    expect(entry.selected).toEqual(expect.arrayContaining([
      expect.objectContaining({ uri: "viking://user/default/memories/high", injected: true }),
    ]));
  });

  it("defaults explicit memory_recall to backward-compatible user and agent memory recall", async () => {
    const openVikingTransport = vi.fn(async (url: string, init?: RequestInit) => {
      const requestUrl = new URL(url);
      if (requestUrl.pathname === "/api/v1/search/find") {
        const body = JSON.parse(String(init?.body ?? "{}"));
        const memories = String(body.context_type ?? "") === "memory"
          ? [makeMemory({ uri: "viking://user/default/memories/project-docs", abstract: "User memory docs", score: 0.9 })]
          : [];
        return okResponse({ memories, resources: [], skills: [], total: memories.length });
      }
      if (requestUrl.pathname === "/api/v1/content/read") {
        return okResponse("Full resource content");
      }
      return okResponse({});
    });

    const { factoryTools, api } = setupPlugin(undefined, { traceRecall: true });
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    const recall = factoryTools.get("memory_recall")!({ sessionId: "test-session", agentId: "main" });
    await recall.execute("tc-recall-resource", { query: "project docs", limit: 1, scoreThreshold: 0.2 });

    const findBodies = openVikingTransport.mock.calls
      .filter(([calledUrl]) => String(calledUrl).includes("/api/v1/search/find"))
      .map(([, init]) => JSON.parse(String((init as RequestInit).body)));
    expect(findBodies).toHaveLength(1);
    expect(findBodies[0]).toMatchObject({ context_type: "memory" });
    expect(findBodies[0]!.target_uri).toBeUndefined();

    const trace = factoryTools.get("ov_recall_trace")!({ sessionId: "test-session" });
    const result = await trace.execute("tc-trace", { source: "memory_recall", limit: 10 }) as ToolResult;
    const entry = (result.details.entries as any[])[0];
    expect(entry.resourceTypes).toEqual(["user"]);
    expect(entry.searches.map((search: any) => search.resourceType)).toEqual(["user"]);
    expect(entry.searches[0].targetUriResolved).toBeUndefined();
  });

  it("allows explicit memory_recall resourceTypes to opt into user recall", async () => {
    const openVikingTransport = vi.fn(async (url: string, init?: RequestInit) => {
      const requestUrl = new URL(url);
      if (requestUrl.pathname === "/api/v1/search/find") {
        const body = JSON.parse(String(init?.body ?? "{}"));
        const memories = String(body.context_type ?? "") === "memory"
          ? [makeMemory({ uri: "viking://user/default/memories/preference", abstract: "User preference", score: 0.9 })]
          : [];
        return okResponse({ memories, resources: [], skills: [], total: memories.length });
      }
      if (requestUrl.pathname === "/api/v1/content/read") {
        return okResponse("Full user preference content");
      }
      return okResponse({});
    });

    const { factoryTools, api } = setupPlugin(undefined, { traceRecall: true });
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    const recall = factoryTools.get("memory_recall")!({ sessionId: "test-session", agentId: "main" });
    await recall.execute("tc-recall-user", {
      query: "user preference",
      limit: 1,
      scoreThreshold: 0.2,
      resourceTypes: ["user"],
    });

    const findBodies = openVikingTransport.mock.calls
      .filter(([calledUrl]) => String(calledUrl).includes("/api/v1/search/find"))
      .map(([, init]) => JSON.parse(String((init as RequestInit).body)));
    expect(findBodies).toHaveLength(1);
    expect(findBodies[0]).toMatchObject({ context_type: "memory" });
    expect(findBodies[0]!.target_uri).toBeUndefined();

    const trace = factoryTools.get("ov_recall_trace")!({ sessionId: "test-session" });
    const result = await trace.execute("tc-trace", { source: "memory_recall", limit: 10 }) as ToolResult;
    const entry = (result.details.entries as any[])[0];
    expect(entry.resourceTypes).toEqual(["user"]);
    expect(entry.searches[0]).toMatchObject({ resourceType: "user" });
    expect(entry.searches[0].targetUriResolved).toBeUndefined();
  });

  it("records archive search traces with displayed archive matches", async () => {
    const openVikingTransport = vi.fn(async (url: string) => {
      const requestUrl = new URL(url);
      if (requestUrl.pathname === "/api/v1/search/grep") {
        return okResponse({
          matches: [{
            line: 12,
            uri: "viking://session/test-session/history/archive_001#L12",
            content: "discussion about recall traces",
          }],
          count: 1,
        });
      }
      return okResponse({});
    });

    const { factoryTools, api } = setupPlugin(undefined, { traceRecall: true });
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);
    const archiveSearch = factoryTools.get("ov_archive_search")!({ sessionId: "test-session", agentId: "main" });
    await archiveSearch.execute("tc-archive", { query: "recall traces" });

    const trace = factoryTools.get("ov_recall_trace")!({ sessionId: "test-session" });
    const result = await trace.execute("tc-trace", { source: "ov_archive_search", limit: 10 }) as ToolResult;

    expect(result.content[0]!.text).toContain("ov_archive_search");
    expect(result.content[0]!.text).toContain("recall traces");
    expect(result.content[0]!.text).toContain("archive_001");
    const entry = (result.details.entries as any[])[0];
    expect(entry.operationType).toBe("archive_grep");
    expect(entry.selected).toEqual(expect.arrayContaining([
      expect.objectContaining({ uri: "viking://session/test-session/history/archive_001#L12", displayed: true }),
    ]));
  });
});

describe("Plugin registration", () => {
  it("registers all 14 default tools", () => {
    const { api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    expect(api.registerTool).toHaveBeenCalledTimes(14);
  });

  it("registers only resource query tools when enabledTools is resource_query", () => {
    const { tools, api } = setupPlugin(undefined, { enabledTools: ["resource_query"] });
    contextEnginePlugin.register(api as any);
    expect(api.registerTool).toHaveBeenCalledTimes(4);
    expect([...tools.keys()].sort()).toEqual(["ov_list", "ov_multi_read", "ov_read", "ov_search"]);
  });

  it("does not register memory tools when disabledTools includes memory group", () => {
    const { tools, api } = setupPlugin(undefined, { disabledTools: ["memory"] });
    contextEnginePlugin.register(api as any);
    expect(tools.get("ov_search")).toBeDefined();
    expect(tools.get("ov_read")).toBeDefined();
    expect(tools.get("memory_recall")).toBeUndefined();
    expect(tools.get("memory_store")).toBeUndefined();
    expect(tools.get("memory_forget")).toBeUndefined();
  });

  it("keeps add_resource opt-in even when all tools are enabled", () => {
    const { tools, api } = setupPlugin(undefined, { enabledTools: ["all"] });
    contextEnginePlugin.register(api as any);
    expect(tools.get("add_resource")).toBeUndefined();
  });

  it("registers add_resource only when both selected and explicitly enabled", () => {
    const { tools, api } = setupPlugin(undefined, {
      enabledTools: ["add_resource"],
      enableAddResourceTool: true,
    });
    contextEnginePlugin.register(api as any);
    expect([...tools.keys()]).toEqual(["add_resource"]);
  });

  it("registers add and search commands", () => {
    const { commands, api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    expect(commands.get("add-resource")).toMatchObject({
      acceptsArgs: true,
      description: "Add a resource into OpenViking.",
    });
    expect(commands.get("add-skill")).toMatchObject({
      acceptsArgs: true,
      description: "Add a skill into OpenViking.",
    });
    expect(commands.get("ov-search")).toMatchObject({
      acceptsArgs: true,
      description: "Search OpenViking resources and skills.",
    });
  });

  it("add and search commands return usage errors when args are missing", async () => {
    const { commands, api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    const resource = await commands.get("add-resource")!.handler({
      args: "",
      commandBody: "/add-resource",
    });
    const skill = await commands.get("add-skill")!.handler({
      args: "",
      commandBody: "/add-skill",
    });
    const search = await commands.get("ov-search")!.handler({
      args: "",
      commandBody: "/ov-search",
    });
    expect(resource.text).toContain("Usage: /add-resource");
    expect(skill.text).toContain("Usage: /add-skill");
    expect(search.text).toContain("Usage: /ov-search");
  });

  it("search command propagates agent identity when command ctx includes it", async () => {
    const openVikingTransport = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/api/v1/search/find")) {
        return okResponse({ memories: [], resources: [], skills: [], total: 0 });
      }
      return okResponse({});
    });

    const { commands, api } = setupPlugin();
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);

    await commands.get("ov-search")!.handler({
      args: "test query --uri viking://resources",
      commandBody: "/ov-search",
      agentId: "worker",
      sessionId: "session-1",
      sessionKey: "agent:worker:session-1",
    });

    const [, init] = openVikingTransport.mock.calls.find((call) => String(call[0]).endsWith("/api/v1/search/find")) as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get("X-OpenViking-Actor-Peer")).toBe("worker");
  });

  it("search command propagates configured tenant headers", async () => {
    const openVikingTransport = vi.fn(async (url: string) => {
      if (url.endsWith("/api/v1/search/find")) {
        return okResponse({ memories: [], resources: [], skills: [], total: 0 });
      }
      return okResponse({});
    });

    const { commands, api } = setupPlugin();
    (api as any).openVikingTransport = openVikingTransport;
    api.pluginConfig = {
      ...api.pluginConfig,
      accountId: "acct-shared",
      userId: "alice",
    };
    contextEnginePlugin.register(api as any);

    await commands.get("ov-search")!.handler({
      args: "test query --uri viking://resources",
      commandBody: "/ov-search",
      agentId: "worker",
      sessionId: "session-1",
      sessionKey: "agent:worker:session-1",
    });

    const [, init] = openVikingTransport.mock.calls.find((call) => String(call[0]).endsWith("/api/v1/search/find")) as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get("X-OpenViking-Account")).toBe("acct-shared");
    expect(headers.get("X-OpenViking-User")).toBe("alice");
    expect(headers.get("X-OpenViking-Actor-Peer")).toBe("worker");
  });

  it("add_resource propagates configured tenant headers", async () => {
    const openVikingTransport = vi.fn(async () =>
      okResponse({ root_uri: "viking://resources/shared-docs", status: "success" }),
    );

    const { tools, api } = setupPlugin(undefined, { enableAddResourceTool: true });
    (api as any).openVikingTransport = openVikingTransport;
    api.pluginConfig = {
      ...api.pluginConfig,
      accountId: "acct-shared",
      userId: "alice",
    };
    contextEnginePlugin.register(api as any);

    const tool = tools.get("add_resource")!;
    await tool.execute("tc-add-resource", {
      source: "https://example.com/docs",
      to: "viking://resources/shared-docs",
      wait: true,
    });

    const [, init] = openVikingTransport.mock.calls.find((call) => String(call[0]).endsWith("/api/v1/resources")) as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get("X-OpenViking-Account")).toBe("acct-shared");
    expect(headers.get("X-OpenViking-User")).toBe("alice");
  });

  it("add_resource uploads local media attachment paths as resources", async () => {
    const tempDir = await mkdtemp(join(tmpdir(), "openclaw-media-"));
    const filePath = join(tempDir, "大秦-TOP20.xlsx");
    await writeFile(filePath, "spreadsheet bytes");

    const openVikingTransport = vi
      .fn()
      .mockResolvedValueOnce(okResponse({ temp_file_id: "upload_sheet.xlsx" }))
      .mockResolvedValueOnce(okResponse({ root_uri: "viking://resources/sheet", status: "success" }));

    try {
      const { tools, api } = setupPlugin(undefined, { enableAddResourceTool: true });
      (api as any).openVikingTransport = openVikingTransport;
      contextEnginePlugin.register(api as any);

      const tool = tools.get("add_resource")!;
      const result = await tool.execute("tc-add-resource-local-media", {
        source: filePath,
        wait: true,
      }) as ToolResult;

      expect(result.content[0]!.text).toContain("Imported OpenViking resource");
      expect(openVikingTransport.mock.calls[0]![0]).toBe("http://127.0.0.1:1933/api/v1/resources/temp_upload");
      expect(openVikingTransport.mock.calls[1]![0]).toBe("http://127.0.0.1:1933/api/v1/resources");
      const body = JSON.parse(String(openVikingTransport.mock.calls[1]![1]!.body));
      expect(body).toMatchObject({
        temp_file_id: "upload_sheet.xlsx",
        wait: true,
      });
    } finally {
      await rm(tempDir, { recursive: true, force: true });
    }
  });

  it("add_skill posts skill imports to the skills API", async () => {
    const openVikingTransport = vi.fn(async () =>
      okResponse({ uri: "viking://user/skills/demo", name: "demo" }),
    );

    const { tools, api } = setupPlugin();
    (api as any).openVikingTransport = openVikingTransport;
    contextEnginePlugin.register(api as any);

    const tool = tools.get("add_skill")!;
    const result = await tool.execute("tc-add-skill", {
      data: "name: demo\n",
      wait: true,
      timeout: 30,
    }) as ToolResult;

    expect(result.content[0]!.text).toContain("Imported OpenViking skill");
    const [url, init] = openVikingTransport.mock.calls.find((call) => String(call[0]).endsWith("/api/v1/skills")) as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:1933/api/v1/skills");
    const body = JSON.parse(String(init.body));
    expect(body).toMatchObject({
      data: "name: demo\n",
      wait: true,
      timeout: 30,
    });
  });

  it("slash commands honor bypassSessionPatterns", async () => {
    const openVikingTransport = vi.fn(async () => okResponse({}));

    const { commands, api } = setupPlugin();
    (api as any).openVikingTransport = openVikingTransport;
    api.pluginConfig = {
      ...api.pluginConfig,
      bypassSessionPatterns: ["agent:bypass:*"],
    };
    contextEnginePlugin.register(api as any);

    const search = await commands.get("ov-search")!.handler({
      args: "test query --uri viking://resources",
      commandBody: "/ov-search",
      sessionKey: "agent:bypass:session-1",
    });

    expect(search.text).toContain("bypassed for this session");
    expect(openVikingTransport).not.toHaveBeenCalled();
  });

  it("registers service with id 'openviking'", () => {
    const { api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    expect(api.registerService).toHaveBeenCalledWith(
      expect.objectContaining({ id: "openviking" }),
    );
  });

  it("registers context engine when api.registerContextEngine is available", () => {
    const { api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    expect(api.registerContextEngine).toHaveBeenCalledWith(
      "openviking",
      expect.any(Function),
    );
  });

  it("registers hooks: session_start, session_end, before_reset, after_compaction", () => {
    const { api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    const hookNames = api.on.mock.calls.map((c: unknown[]) => c[0]);
    expect(hookNames).toContain("session_start");
    expect(hookNames).toContain("session_end");
    expect(hookNames).toContain("before_reset");
    expect(hookNames).toContain("after_compaction");
    expect(hookNames).not.toContain("agent_end");
    expect(hookNames).not.toContain("before_prompt_build");
  });

  it("plugin has correct metadata", () => {
    expect(contextEnginePlugin.id).toBe("openviking");
    expect(contextEnginePlugin.kind).toBe("context-engine");
    expect(contextEnginePlugin.name).toContain("OpenViking");
  });
});

describe("Tool: memory_forget (error paths)", () => {
  it("factory-created forget tool requires either uri or query", async () => {
    const { tools, api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    const forget = tools.get("memory_forget");
    expect(forget).toBeDefined();

    // memory_forget is a direct tool (not factory), so execute is available
    // but depends on getClient. The error path for missing params doesn't need client.
    const result = await forget!.execute("tc1", {}) as ToolResult;
    expect(result.content[0]!.text).toBe("Provide uri or query.");
    expect(result.details.error).toBe("missing_param");
  });
});
