import { afterEach, describe, expect, it, vi } from "vitest";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import contextEnginePlugin, {
  parseAddResourceCommandArgs,
  parseAddSkillCommandArgs,
  parseMemorySearchCommandArgs,
  tokenizeCommandArgs,
} from "../../index.js";
import type { FindResultItem } from "../../client.js";

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

afterEach(() => {
  vi.unstubAllGlobals();
});

function setupPlugin(clientOverrides?: Record<string, unknown>) {
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
    registerService: vi.fn(),
    registerContextEngine: vi.fn(),
    on: vi.fn(),
  };

  // Patch the module-level getClient
  const originalRegister = contextEnginePlugin.register.bind(contextEnginePlugin);

  // We need to intercept the getClient inside register. Since register() creates
  // the client promise internally, we mock the global module state.
  // For remote mode, it creates: clientPromise = Promise.resolve(new OpenVikingClient(...))
  // We can't easily mock that. Instead, let's rely on the fact that remote mode
  // creates a real client. We'll mock at the fetch level or just test the logic.

  // Simpler approach: since the tools are closures, we need to register the plugin
  // and then replace the client. But that's hard with closures.

  // Best approach: Test the tool execute functions by extracting them from the
  // captured registerTool calls. The getClient() inside them will try to create
  // a real client for remote mode. We need to mock fetch or accept that these
  // tests focus on the logic, not the HTTP calls.

  // Actually, for testing, we can override the global fetch to return mock responses.
  // But let's keep it simple and test the execution flow with proper mocking.

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
});

describe("Tool: memory_store (behavioral)", () => {
  it("registers with correct name and description", () => {
    const { tools, api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    const store = tools.get("memory_store");
    expect(store).toBeDefined();
    expect(store!.name).toBe("memory_store");
    expect(store!.description).toContain("Store text");
  });

  it("uses requesterSenderId to populate role_id for user writes", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
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
    vi.stubGlobal("fetch", fetchMock);

    const { factoryTools, api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    const factory = factoryTools.get("memory_store");
    expect(factory).toBeDefined();

    const tool = factory!({
      sessionId: "runtime-session",
      sessionKey: "agent:main:main",
      requesterSenderId: "wx/user-01@abc",
    });

    await tool.execute("tc-memory-store", { text: "hello from tool" });

    const messageCall = fetchMock.mock.calls.find(([url]) =>
      String(url).includes("/api/v1/sessions/") && String(url).includes("/messages"),
    );
    expect(messageCall).toBeDefined();
    const [, init] = messageCall as [string, RequestInit];
    const body = JSON.parse(String(init.body));
    expect(body.role).toBe("user");
    expect(body.role_id).toBe("wx_user-01_abc");
  });

  it("uses a temporary session by default instead of the current tool session", async () => {
    const fetchMock = vi.fn(async (url: string) => {
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
    vi.stubGlobal("fetch", fetchMock);

    const { factoryTools, api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    const tool = factoryTools.get("memory_store")!({
      sessionId: "runtime-session",
      sessionKey: "agent:main:main",
    });

    await tool.execute("tc-memory-store", { text: "hello from tool" });

    const messageCall = fetchMock.mock.calls.find(([url]) =>
      String(url).includes("/api/v1/sessions/") && String(url).includes("/messages"),
    );
    expect(String(messageCall?.[0])).toContain("/api/v1/sessions/memory-store-");
  });

  it("normalizes explicit memory_store sessionId without using current sessionKey", async () => {
    const fetchMock = vi.fn(async (url: string) => {
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
    vi.stubGlobal("fetch", fetchMock);

    const { factoryTools, api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    const tool = factoryTools.get("memory_store")!({
      sessionId: "runtime-session",
      sessionKey: "agent:main:main",
    });

    await tool.execute("tc-memory-store", {
      text: "hello from tool",
      sessionId: "C:\\Users\\test",
    });

    const messageCall = fetchMock.mock.calls.find(([url]) =>
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

describe("Tool: add_resource, add_skill, and memory_search (registration)", () => {
  it("registers add_resource tool with expected parameters", () => {
    const { tools, api } = setupPlugin();
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

  it("registers memory_search tool with natural-language trigger guidance", () => {
    const { tools, api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    const tool = tools.get("memory_search");
    expect(tool).toBeDefined();
    expect(tool!.description).toContain("Search OpenViking resources and skills");
    expect(tool!.description).toContain("Use after importing");
    const props = (tool!.parameters as any).properties;
    expect(props).toHaveProperty("query");
    expect(props).toHaveProperty("uri");
    expect(props).toHaveProperty("limit");
  });
});

describe("Tool: memory_search (behavioral)", () => {
  it("searches resources and skills by default when no uri is provided", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
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
              uri: "viking://agent/skills/install-openviking-memory",
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
    vi.stubGlobal("fetch", fetchMock);

    const { tools, api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    const search = tools.get("memory_search")!;
    const result = await search.execute("tc1", { query: "OpenViking install" }) as ToolResult;

    expect(result.content[0]!.text).toContain("no");
    expect(result.content[0]!.text).toContain("type");
    expect(result.content[0]!.text).toContain("resource");
    expect(result.content[0]!.text).toContain("skill");
    expect(result.details.resources).toHaveLength(1);
    expect(result.details.skills).toHaveLength(1);

    const findBodies = fetchMock.mock.calls
      .filter((call) => String(call[0]).endsWith("/api/v1/search/find"))
      .map((call) => JSON.parse(String((call[1] as RequestInit).body)));
    expect(findBodies.some((body) => body.target_uri === "viking://resources")).toBe(true);
    expect(findBodies.some((body) => String(body.target_uri).startsWith("viking://agent/") && String(body.target_uri).endsWith("/skills"))).toBe(true);
  });

  it("returns partial results when one default scope search fails", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
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
    vi.stubGlobal("fetch", fetchMock);

    const { tools, api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    const search = tools.get("memory_search")!;
    const result = await search.execute("tc1", { query: "OpenViking install" }) as ToolResult;

    expect(result.details.resources).toHaveLength(1);
    expect(result.details.skills).toHaveLength(0);
    expect(result.content[0]!.text).toContain("resource");
  });

  it("renders memory hits when explicit uri returns memories", async () => {
    const fetchMock = vi.fn(async (url: string) => {
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
    vi.stubGlobal("fetch", fetchMock);

    const { tools, api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    const search = tools.get("memory_search")!;
    const result = await search.execute("tc1", {
      query: "theme",
      uri: "viking://user/default/memories",
    }) as ToolResult;

    expect(result.details.memories).toHaveLength(1);
    expect(result.content[0]!.text).toContain("memory");
    expect(result.content[0]!.text).toContain("User prefers dark theme");
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

describe("OpenViking memory_search command parsing", () => {
  it("parses memory_search query and flags", () => {
    expect(parseMemorySearchCommandArgs(`"OpenViking install" --uri viking://resources --limit=3`)).toMatchObject({
      query: "OpenViking install",
      uri: "viking://resources",
      limit: 3,
    });
  });

  it("keeps multi-word unquoted slash-command queries intact", () => {
    expect(parseMemorySearchCommandArgs(`OpenViking install --uri viking://resources`)).toMatchObject({
      query: "OpenViking install",
      uri: "viking://resources",
    });
  });
});

describe("Plugin registration", () => {
  it("registers all 8 tools", () => {
    const { api } = setupPlugin();
    contextEnginePlugin.register(api as any);
    expect(api.registerTool).toHaveBeenCalledTimes(8);
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
    expect(commands.get("memory-search")).toMatchObject({
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
    const search = await commands.get("memory-search")!.handler({
      args: "",
      commandBody: "/memory-search",
    });
    expect(resource.text).toContain("Usage: /add-resource");
    expect(skill.text).toContain("Usage: /add-skill");
    expect(search.text).toContain("Usage: /memory-search");
  });

  it("search command propagates agent identity when command ctx includes it", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/api/v1/search/find")) {
        return okResponse({ memories: [], resources: [], skills: [], total: 0 });
      }
      return okResponse({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const { commands, api } = setupPlugin();
    contextEnginePlugin.register(api as any);

    await commands.get("memory-search")!.handler({
      args: "test query --uri viking://resources",
      commandBody: "/memory-search",
      agentId: "worker",
      sessionId: "session-1",
      sessionKey: "agent:worker:session-1",
    });

    const [, init] = fetchMock.mock.calls.find((call) => String(call[0]).endsWith("/api/v1/search/find")) as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get("X-OpenViking-Agent")).toBe("worker");
  });

  it("search command propagates configured tenant headers", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url.endsWith("/api/v1/search/find")) {
        return okResponse({ memories: [], resources: [], skills: [], total: 0 });
      }
      return okResponse({});
    });
    vi.stubGlobal("fetch", fetchMock);

    const { commands, api } = setupPlugin();
    api.pluginConfig = {
      ...api.pluginConfig,
      accountId: "acct-shared",
      userId: "alice",
    };
    contextEnginePlugin.register(api as any);

    await commands.get("memory-search")!.handler({
      args: "test query --uri viking://resources",
      commandBody: "/memory-search",
      agentId: "worker",
      sessionId: "session-1",
      sessionKey: "agent:worker:session-1",
    });

    const [, init] = fetchMock.mock.calls.find((call) => String(call[0]).endsWith("/api/v1/search/find")) as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get("X-OpenViking-Account")).toBe("acct-shared");
    expect(headers.get("X-OpenViking-User")).toBe("alice");
    expect(headers.get("X-OpenViking-Agent")).toBe("worker");
  });

  it("add_resource propagates configured tenant headers", async () => {
    const fetchMock = vi.fn(async () =>
      okResponse({ root_uri: "viking://resources/shared-docs", status: "success" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { tools, api } = setupPlugin();
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

    const [, init] = fetchMock.mock.calls.find((call) => String(call[0]).endsWith("/api/v1/resources")) as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get("X-OpenViking-Account")).toBe("acct-shared");
    expect(headers.get("X-OpenViking-User")).toBe("alice");
  });

  it("add_resource uploads local media attachment paths as resources", async () => {
    const tempDir = await mkdtemp(join(tmpdir(), "openclaw-media-"));
    const filePath = join(tempDir, "大秦-TOP20.xlsx");
    await writeFile(filePath, "spreadsheet bytes");

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(okResponse({ temp_file_id: "upload_sheet.xlsx" }))
      .mockResolvedValueOnce(okResponse({ root_uri: "viking://resources/sheet", status: "success" }));
    vi.stubGlobal("fetch", fetchMock);

    try {
      const { tools, api } = setupPlugin();
      contextEnginePlugin.register(api as any);

      const tool = tools.get("add_resource")!;
      const result = await tool.execute("tc-add-resource-local-media", {
        source: filePath,
        wait: true,
      }) as ToolResult;

      expect(result.content[0]!.text).toContain("Imported OpenViking resource");
      expect(fetchMock.mock.calls[0]![0]).toBe("http://127.0.0.1:1933/api/v1/resources/temp_upload");
      expect(fetchMock.mock.calls[1]![0]).toBe("http://127.0.0.1:1933/api/v1/resources");
      const body = JSON.parse(String(fetchMock.mock.calls[1]![1]!.body));
      expect(body).toMatchObject({
        temp_file_id: "upload_sheet.xlsx",
        wait: true,
      });
    } finally {
      await rm(tempDir, { recursive: true, force: true });
    }
  });

  it("add_skill posts skill imports to the skills API", async () => {
    const fetchMock = vi.fn(async () =>
      okResponse({ uri: "viking://agent/skills/demo", name: "demo" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { tools, api } = setupPlugin();
    contextEnginePlugin.register(api as any);

    const tool = tools.get("add_skill")!;
    const result = await tool.execute("tc-add-skill", {
      data: "name: demo\n",
      wait: true,
      timeout: 30,
    }) as ToolResult;

    expect(result.content[0]!.text).toContain("Imported OpenViking skill");
    const [url, init] = fetchMock.mock.calls.find((call) => String(call[0]).endsWith("/api/v1/skills")) as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:1933/api/v1/skills");
    const body = JSON.parse(String(init.body));
    expect(body).toMatchObject({
      data: "name: demo\n",
      wait: true,
      timeout: 30,
    });
  });

  it("slash commands honor bypassSessionPatterns", async () => {
    const fetchMock = vi.fn(async () => okResponse({}));
    vi.stubGlobal("fetch", fetchMock);

    const { commands, api } = setupPlugin();
    api.pluginConfig = {
      ...api.pluginConfig,
      bypassSessionPatterns: ["agent:bypass:*"],
    };
    contextEnginePlugin.register(api as any);

    const search = await commands.get("memory-search")!.handler({
      args: "test query --uri viking://resources",
      commandBody: "/memory-search",
      sessionKey: "agent:bypass:session-1",
    });

    expect(search.text).toContain("bypassed for this session");
    expect(fetchMock).not.toHaveBeenCalled();
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
