import { describe, expect, it, vi } from "vitest";

import {
  createOpenVikingCommandDefinitions,
} from "../../plugin/openviking-command-definitions.js";
import { registerOpenVikingContextEngine } from "../../plugin/openviking-context-engine-registration.js";
import { createOpenVikingQueryConfigCommandHandler } from "../../plugin/openviking-query-config-command.js";
import { createOpenVikingQueryRuntime } from "../../plugin/openviking-query-runtime.js";
import { createOpenVikingRecallTraceRuntime } from "../../plugin/openviking-recall-trace-runtime.js";
import {
  createOpenVikingToolRegistrar,
} from "../../plugin/tool-registration.js";
import {
  registerOpenVikingCommands,
} from "../../plugin/command-registration.js";
import {
  RECALL_TRACE_ROUTE_PATHS,
  registerRecallTraceRoutes,
} from "../../plugin/recall-trace-routes.js";
import { createOpenVikingService } from "../../plugin/openviking-services.js";
import { registerOpenVikingArchiveTools } from "../../plugin/openviking-archive-tools.js";
import { registerOpenVikingImportTools } from "../../plugin/openviking-import-tools.js";
import { createOpenVikingImportRuntime } from "../../plugin/openviking-import-runtime.js";
import { registerOpenVikingLifecycleHooks } from "../../plugin/openviking-lifecycle-hooks.js";
import { registerOpenVikingMemoryTools } from "../../plugin/openviking-memory-tools.js";
import { registerOpenVikingMemoryRecallTools } from "../../plugin/openviking-memory-recall-tools.js";
import { registerOpenVikingQueryTools } from "../../plugin/openviking-query-tools.js";
import { registerOpenVikingRecallTraceTools } from "../../plugin/openviking-recall-trace-tools.js";
import { registerOpenVikingToolResultTools } from "../../plugin/openviking-tool-result-tools.js";

describe("plugin module seams", () => {
  it("registers only enabled OpenViking tools through the tool-registration seam", () => {
    const api = { registerTool: vi.fn() };
    const logger = { debug: vi.fn() };
    const registrar = createOpenVikingToolRegistrar({
      api,
      enabledToolNames: new Set(["ov_search"]),
      logger,
    });
    const toolFactory = () => ({ name: "ov_search" });
    const disabledFactory = () => ({ name: "memory_store" });

    registrar(toolFactory, { name: "ov_search" });
    registrar(disabledFactory, { name: "memory_store" });

    expect(api.registerTool).toHaveBeenCalledTimes(1);
    expect(api.registerTool).toHaveBeenCalledWith(toolFactory, { name: "ov_search" });
    expect(logger.debug).toHaveBeenCalledWith("openviking: tool memory_store disabled by config");
  });

  it("registers command definitions without changing names or handlers", async () => {
    const command = {
      name: "ov-search",
      description: "Search OpenViking resources and skills.",
      acceptsArgs: true,
      handler: vi.fn().mockResolvedValue({ text: "ok" }),
    };
    const api = { registerCommand: vi.fn() };

    registerOpenVikingCommands(api, [command]);

    expect(api.registerCommand).toHaveBeenCalledWith(command);
    const registered = api.registerCommand.mock.calls[0]?.[0];
    await expect(registered.handler({ args: "query" })).resolves.toEqual({ text: "ok" });
    expect(command.handler).toHaveBeenCalledWith({ args: "query" });
  });

  it("builds OpenViking command definitions through a dedicated plugin module", async () => {
    const addResourceOpenViking = vi.fn().mockResolvedValue({
      content: [{ type: "text" as const, text: "resource imported" }],
      details: { action: "resource_imported" },
    });
    const addSkillOpenViking = vi.fn().mockResolvedValue({
      content: [{ type: "text" as const, text: "skill imported" }],
      details: { action: "skill_imported" },
    });
    const searchOpenViking = vi.fn().mockResolvedValue({
      content: [{ type: "text" as const, text: "search result" }],
      details: { action: "searched" },
    });
    const handleQueryConfigCommand = vi.fn().mockResolvedValue({ text: "query config", details: { action: "query_config" } });
    const queryRecallTraces = vi.fn().mockResolvedValue({ entries: [{ traceId: "trace-1" }], lookupLayer: "memory", warnings: [] });
    const formatRecallTraceText = vi.fn().mockReturnValue("trace text");
    const deps = {
      resolvePluginSessionRouting: () => ({ agentId: "agent-main", sessionId: "session-1", ovSessionId: "ov-session-1" }),
      isBypassedSession: () => false,
      makeBypassedToolResult: (toolName: string) => ({ content: [{ type: "text" as const, text: `bypassed ${toolName}` }], details: { toolName } }),
      parseAddResourceCommandArgs: vi.fn().mockReturnValue({ source: "https://example.com/doc", wait: true }),
      parseAddSkillCommandArgs: vi.fn().mockReturnValue({ source: "skill.md" }),
      parseOVSearchCommandArgs: vi.fn().mockReturnValue({ query: "docs", uri: "viking://resources", limit: 3 }),
      addResourceOpenViking,
      addSkillOpenViking,
      searchOpenViking,
      handleQueryConfigCommand,
      queryRecallTraces,
      formatRecallTraceText,
    };

    const commands = createOpenVikingCommandDefinitions(deps);

    expect(commands.map((command) => command.name)).toEqual([
      "add-resource",
      "add-skill",
      "ov-search",
      "ov-query-config",
      "ov-recall-trace",
    ]);

    await expect(commands[0]!.handler({ args: "https://example.com/doc", sessionId: "session-1" })).resolves.toEqual({
      text: "resource imported",
      details: { action: "resource_imported" },
    });
    expect(addResourceOpenViking).toHaveBeenCalledWith({ source: "https://example.com/doc", wait: true }, "agent-main");

    await commands[1]!.handler({ args: "skill.md" });
    expect(addSkillOpenViking).toHaveBeenCalledWith({ source: "skill.md" }, "agent-main");

    await commands[2]!.handler({ args: "docs --uri viking://resources --limit 3" });
    expect(searchOpenViking).toHaveBeenCalledWith({ query: "docs", uri: "viking://resources", limit: 3 }, "agent-main", {
      agentId: "agent-main",
      sessionId: "session-1",
      ovSessionId: "ov-session-1",
    });

    await expect(commands[3]!.handler({ args: "get" })).resolves.toEqual({ text: "query config", details: { action: "query_config" } });
    expect(handleQueryConfigCommand).toHaveBeenCalledWith({ args: "get" });

    await expect(commands[4]!.handler({ args: "--source ov_search --limit 5" })).resolves.toEqual({
      text: "trace text",
      details: { count: 1, lookupLayer: "memory", warnings: [], entries: [{ traceId: "trace-1" }] },
    });
    expect(queryRecallTraces).toHaveBeenCalledWith(expect.objectContaining({ source: "ov_search", limit: 5, includeContent: false }), {
      agentId: "agent-main",
      sessionId: "session-1",
      ovSessionId: "ov-session-1",
    });
    expect(formatRecallTraceText).toHaveBeenCalledWith({ entries: [{ traceId: "trace-1" }], lookupLayer: "memory", warnings: [] });
  });

  it("keeps recall trace route paths stable across legacy and HTTP adapters", () => {
    expect(RECALL_TRACE_ROUTE_PATHS).toEqual([
      "/api/openviking/recall-traces",
      "/api/openviking/uri-detail",
      "/api/openviking/recall-traces/latest-ov-search-list",
      "/api/openviking/recall-traces/:traceId",
    ]);

    const adapter = {
      registerRoute: vi.fn(),
      registerHttpRoute: vi.fn(),
    };
    const handlers = {
      handleRecallTraces: vi.fn().mockResolvedValue({ status: 200, body: { ok: true } }),
      handleUriDetail: vi.fn().mockResolvedValue({ status: 200, body: { ok: true } }),
      handleLatestOvSearchList: vi.fn().mockResolvedValue({ status: 200, body: { ok: true } }),
    };

    const registered = registerRecallTraceRoutes(adapter, handlers);

    expect(registered).toBe(true);
    expect(adapter.registerRoute.mock.calls.map(([route]) => route.path)).toEqual(RECALL_TRACE_ROUTE_PATHS);
    expect(adapter.registerHttpRoute.mock.calls.map(([route]) => route.path)).toEqual([
      "/api/openviking/recall-traces",
      "/api/openviking/uri-detail",
      "/api/openviking/recall-traces/latest-ov-search-list",
      "/api/openviking/recall-traces",
    ]);
  });

  it("creates recall trace route handlers through a dedicated runtime module", async () => {
    const entry = {
      schemaVersion: "1.0" as const,
      traceId: "trace-1",
      ts: Date.UTC(2026, 0, 1),
      sessionId: "session-1",
      sessionKey: "session-key-1",
      ovSessionId: "ov-session-1",
      agentId: "agent-main",
      source: "ov_search" as const,
      operationType: "semantic_find" as const,
      resourceTypes: ["resource" as const],
      trigger: { query: "project spec" },
      searches: [{
        resourceType: "resource" as const,
        targetUriResolved: "viking://resources",
        limit: 5,
        durationMs: 1,
        total: 2,
        results: [
          { uri: "viking://resources/project/spec.md", resourceType: "resource" as const, category: "doc", score: 0.91, abstractPreview: "Search abstract", resultType: "resource" as const },
          { uri: "viking://user/skills/debugger", resourceType: "agent" as const, category: "skill", score: 0.8, abstractPreview: "Skill abstract", resultType: "skill" as const },
        ],
      }],
      selected: [{ uri: "viking://resources/project/spec.md", resourceType: "resource" as const, category: "selected-doc", score: 0.95, abstractPreview: "Selected abstract" }],
      stats: { candidateCount: 2, selectedCount: 1, injectedCount: 0 },
    };
    const traceRecorder = {
      queryWithFallback: vi.fn().mockResolvedValue({ entries: [entry], lookupLayer: "memory", warnings: [] }),
    };
    const read = vi.fn().mockResolvedValue("0123456789abcdefghijklmnopqrstuvwxyz");
    const runtime = createOpenVikingRecallTraceRuntime({
      getClient: async () => ({ read }),
      resolvePluginSessionRouting: () => ({ agentId: "agent-main", sessionId: "session-1", sessionKey: "session-key-1", ovSessionId: "ov-session-1" }),
      traceRecorder,
      registerRecallTraceRoutes: vi.fn().mockReturnValue(true),
      normalizeResourceTypes: (value) => Array.isArray(value) ? value : [String(value)],
      clampScore: (value) => value,
      previewText: (value, maxChars) => typeof value === "string" ? value.slice(0, maxChars) : undefined,
      cfg: { traceRecallIncludeContentByDefault: true, traceRecallPreviewChars: 80, recallMaxContentChars: 100 },
    });

    const list = await runtime.routeHandlers.handleLatestOvSearchList({ query: { sessionkey: "session-key-1", includeSkills: "false", limit: "5" } }) as { status: number; body: any };
    expect(list.status).toBe(200);
    expect(list.body.items).toHaveLength(1);
    expect(list.body.items[0]).toMatchObject({
      uri: "viking://resources/project/spec.md",
      abstractPreview: "Selected abstract",
      source: "selected",
      targetUri: "viking://resources",
    });

    const detail = await runtime.routeHandlers.handleUriDetail({ query: { uri: "viking://resources/project/spec.md", traceId: "trace-1", offset: "10", contentLimit: "5" } }) as { status: number; body: any };
    expect(detail.status).toBe(200);
    expect(detail.body.uriType).toBe("resource");
    expect(detail.body.metadata).toMatchObject({ category: "selected-doc", sourceTraceId: "trace-1", source: "ov_search" });
    expect(detail.body.content).toMatchObject({ text: "abcde", offset: 10, limit: 5, hasMore: true });

    read.mockClear();
    const kebabDetail = await runtime.routeHandlers.handleUriDetail({ query: { uri: "viking://resources/project/spec.md", "trace-id": "trace-1", offset: "10", "content-limit": "5", "include-content": "false" } }) as { status: number; body: any };
    expect(kebabDetail.status).toBe(200);
    expect(kebabDetail.body.metadata).toMatchObject({ category: "selected-doc", sourceTraceId: "trace-1", source: "ov_search" });
    expect(kebabDetail.body).not.toHaveProperty("content");
    expect(read).not.toHaveBeenCalled();

    traceRecorder.queryWithFallback.mockClear();
    read.mockClear();
    const traces = await runtime.routeHandlers.handleRecallTraces({ query: { "trace-id": "trace-1", "session-key": "session-key-1", "include-content": "false", limit: "1" } }) as { status: number; body: any };
    expect(traces.status).toBe(200);
    expect(traceRecorder.queryWithFallback).toHaveBeenCalledWith(expect.objectContaining({
      traceId: "trace-1",
      sessionKey: "session-key-1",
      limit: 1,
    }));
    expect(read).not.toHaveBeenCalled();
  });

  it("creates OpenViking query runtime for search/read behavior", async () => {
    const find = vi.fn(async (_query: string, options: { targetUri?: string }) => {
      if (options.targetUri === "viking://resources") {
        return {
          memories: [],
          resources: [{ uri: "viking://resources/spec.md", abstract: "Spec abstract", score: 0.91, category: "doc" }],
          skills: [],
          total: 1,
        };
      }
      return {
        memories: [],
        resources: [],
        skills: [{ uri: "viking://user/skills/debugger", overview: "Debugger overview", score: 0.8, category: "skill" }],
        total: 1,
      };
    });
    const read = vi.fn().mockResolvedValue({ text: "full content" });
    const recordAndFlush = vi.fn();
    const runtime = createOpenVikingQueryRuntime({
      getClient: async () => ({ find, read, list: vi.fn().mockResolvedValue([]) }),
      queryConfigStore: { getEffective: vi.fn().mockResolvedValue({ ovSearchLimit: 2 }) },
      toQueryConfigContext: (session) => session,
      traceRecorder: { recordAndFlush },
      inferRecallResourceType: (uri) => uri.includes("skills") ? "agent" : "resource",
      createTraceId: () => "trace-query-1",
      boundTraceQuery: (query) => ({ query }),
      previewText: (value) => typeof value === "string" ? value : undefined,
      logger: { warn: vi.fn() },
      cfg: { traceRecallMaxResultsPerSearch: 5, traceRecallPreviewChars: 80, traceRecallQueryMaxChars: 120 },
    });

    const search = await runtime.searchOpenViking({ query: "spec" }, "agent-main", { agentId: "agent-main", sessionId: "session-1" }) as any;
    expect(find.mock.calls.map(([, options]) => options.targetUri)).toEqual(["viking://resources", "viking://user/skills"]);
    expect(search.content[0].text).toContain("Found 2 OpenViking results for \"spec\"");
    expect(search.details).toMatchObject({ action: "searched", total: 2 });
    expect(recordAndFlush).toHaveBeenCalledWith(expect.objectContaining({
      traceId: "trace-query-1",
      source: "ov_search",
      agentId: "agent-main",
      stats: { candidateCount: 2, selectedCount: 2, injectedCount: 0 },
    }));

    const readResult = await runtime.readOpenVikingContent({ uri: "viking://resources/spec.md" }, "agent-main") as any;
    expect(read).toHaveBeenCalledWith("viking://resources/spec.md", "agent-main");
    expect(readResult.content[0].text).toContain("--- START OF viking://resources/spec.md ---");
  });

  it("handles OpenViking query config commands through a dedicated module", async () => {
    const session = { agentId: "agent-main", sessionId: "session-1", sessionKey: "key-1", ovSessionId: "ov-session-1" };
    const queryCtx = { agentId: "agent-main", sessionId: "session-1", sessionKey: "key-1", ovSessionId: "ov-session-1" };
    const effective = { ovSearchLimit: 7, targetUri: "viking://resources", warnings: [] };
    const queryConfigStore = {
      getEffective: vi.fn().mockResolvedValue(effective),
      set: vi.fn().mockResolvedValue(undefined),
      unset: vi.fn().mockResolvedValue(undefined),
      reset: vi.fn().mockResolvedValue(undefined),
    };
    const normalizeRuntimeQueryParams = vi.fn().mockReturnValue({ params: { ovSearchLimit: 3 }, warnings: ["normalized"] });
    const handler = createOpenVikingQueryConfigCommandHandler({
      resolvePluginSessionRouting: vi.fn().mockReturnValue(session),
      toQueryConfigContext: vi.fn().mockReturnValue(queryCtx),
      queryConfigStore,
      normalizeRuntimeQueryParams,
    });

    await expect(handler({ args: "get --scope claw" })).resolves.toEqual({
      text: JSON.stringify({ scope: "claw", effective }, null, 2),
      details: { scope: "claw", effective },
    });

    await expect(handler({ args: "set --ovSearchLimit 3 --scope session" })).resolves.toMatchObject({
      text: "Updated OpenViking query config (session). Warnings: normalized",
      details: { scope: "session", params: { ovSearchLimit: 3 }, warnings: ["normalized"], effective },
    });
    expect(normalizeRuntimeQueryParams).toHaveBeenCalledWith({ ovSearchLimit: 3 });
    expect(queryConfigStore.set).toHaveBeenCalledWith("session", queryCtx, { ovSearchLimit: 3 });

    await expect(handler({ args: "unset ovSearchLimit targetUri --scope claw" })).resolves.toMatchObject({
      text: "Unset OpenViking query config fields (claw): ovSearchLimit, targetUri",
      details: { scope: "claw", fields: ["ovSearchLimit", "targetUri"], effective },
    });
    expect(queryConfigStore.unset).toHaveBeenCalledWith("claw", queryCtx, ["ovSearchLimit", "targetUri"]);

    await expect(handler({ args: "reset" })).resolves.toMatchObject({
      text: "Reset OpenViking query config (session).",
      details: { scope: "session", effective },
    });
    expect(queryConfigStore.reset).toHaveBeenCalledWith("session", queryCtx);
  });

  it("creates OpenViking import runtime for command import behavior", async () => {
    const addResource = vi.fn().mockResolvedValue({
      root_uri: "viking://resources/project-docs",
      warnings: ["kept existing metadata"],
      task_id: "task-resource-1",
    });
    const addSkill = vi.fn().mockResolvedValue({
      uri: "viking://user/skills/debugger",
      name: "debugger",
      task_id: "task-skill-1",
    });
    const runtime = createOpenVikingImportRuntime({
      getClient: async () => ({ addResource, addSkill }),
    });

    await expect(runtime.addResourceOpenViking({
      source: "./docs",
      to: "viking://resources/project-docs",
      parent: "viking://resources",
      reason: "project docs",
      instruction: "extract API notes",
      wait: true,
      timeout: 30,
    }, "agent-main")).resolves.toEqual({
      content: [{ type: "text", text: "Imported OpenViking resource. viking://resources/project-docs Warnings: kept existing metadata" }],
      details: {
        action: "resource_imported",
        root_uri: "viking://resources/project-docs",
        warnings: ["kept existing metadata"],
        task_id: "task-resource-1",
      },
    });
    expect(addResource).toHaveBeenCalledWith({
      pathOrUrl: "./docs",
      to: "viking://resources/project-docs",
      parent: "viking://resources",
      reason: "project docs",
      instruction: "extract API notes",
      wait: true,
      timeout: 30,
    }, "agent-main");

    await expect(runtime.addSkillOpenViking({
      source: "./skills/debugger/SKILL.md",
      data: "skill body",
      wait: false,
      timeout: 5,
    }, "agent-main")).resolves.toEqual({
      content: [{ type: "text", text: "Imported OpenViking skill (debugger). viking://user/skills/debugger" }],
      details: {
        action: "skill_imported",
        uri: "viking://user/skills/debugger",
        name: "debugger",
        task_id: "task-skill-1",
      },
    });
    expect(addSkill).toHaveBeenCalledWith({
      path: "./skills/debugger/SKILL.md",
      data: "skill body",
      wait: false,
      timeout: 5,
    }, "agent-main");
  });

  it("creates the OpenViking lifecycle service without changing start/stop behavior", async () => {
    const healthCheck = vi.fn().mockResolvedValue({ ok: true });
    const logger = { info: vi.fn(), warn: vi.fn() };
    const registerRecallTraceRoutes = vi.fn().mockReturnValue(true);
    const service = createOpenVikingService({
      cfg: { baseUrl: "http://127.0.0.1:1933", targetUri: "viking://resources" },
      getClient: async () => ({ healthCheck }),
      logger,
      recallTraceHttpRoutesRegistered: false,
      registerRecallTraceRoutes,
    });

    expect(service.id).toBe("openviking");
    await service.start({ registerRoute: vi.fn() });
    service.stop();

    expect(registerRecallTraceRoutes).toHaveBeenCalled();
    expect(healthCheck).toHaveBeenCalled();
    expect(logger.info).toHaveBeenCalledWith(expect.stringContaining("openviking: initialized"));
    expect(logger.info).toHaveBeenCalledWith("openviking: registered recall trace Gateway routes");
    expect(logger.info).toHaveBeenCalledWith("openviking: stopped");
  });

  it("registers lifecycle hooks through a dedicated plugin module", async () => {
    const handlers = new Map<string, Function>();
    const api = {
      on: vi.fn((hookName: string, handler: Function) => {
        handlers.set(hookName, handler);
      }),
    };
    const rememberSessionAgentId = vi.fn();
    const verboseRoutingInfo = vi.fn();
    const commitOVSession = vi.fn().mockResolvedValue(true);
    const logger = { info: vi.fn(), warn: vi.fn() };

    registerOpenVikingLifecycleHooks({
      api,
      rememberSessionAgentId,
      isBypassedSession: (ctx) => ctx?.sessionKey === "bypass",
      verboseRoutingInfo,
      getContextEngine: () => ({ commitOVSession }),
      logger,
    });

    expect(api.on.mock.calls.map(([hookName]) => hookName)).toEqual([
      "session_start",
      "session_end",
      "before_reset",
      "after_compaction",
    ]);

    await handlers.get("session_start")?.({}, { sessionId: "session-1", agentId: "agent-main" });
    await handlers.get("session_end")?.({}, { sessionId: "session-2", agentId: "agent-main" });
    expect(rememberSessionAgentId).toHaveBeenCalledWith({ sessionId: "session-1", agentId: "agent-main" });
    expect(rememberSessionAgentId).toHaveBeenCalledWith({ sessionId: "session-2", agentId: "agent-main" });

    await handlers.get("before_reset")?.({}, { sessionId: "session-3", sessionKey: "key-3" });
    expect(commitOVSession).toHaveBeenCalledWith({ sessionId: "session-3", sessionKey: "key-3" });
    expect(logger.info).toHaveBeenCalledWith("openviking: committed OV session on reset for session=session-3");

    await handlers.get("before_reset")?.({}, { sessionId: "session-4", sessionKey: "bypass" });
    expect(verboseRoutingInfo).toHaveBeenCalledWith(expect.stringContaining("bypassing before_reset"));
    expect(commitOVSession).toHaveBeenCalledTimes(1);
  });

  it("registers the context engine through a dedicated plugin module", () => {
    const engine = { id: "openviking", commitOVSession: vi.fn() };
    const api = { registerContextEngine: vi.fn() };
    const logger = { info: vi.fn(), warn: vi.fn() };
    const getClient = vi.fn();
    const resolveAgentId = vi.fn();
    const rememberSessionAgentId = vi.fn();
    const queryConfigStore = { get: vi.fn() };
    const traceRecorder = { record: vi.fn() };
    const createContextEngine = vi.fn().mockReturnValue(engine);
    const setContextEngineRef = vi.fn();

    registerOpenVikingContextEngine({
      api,
      plugin: { id: "openviking", name: "OpenViking" },
      version: "0.1.0",
      cfg: { baseUrl: "http://127.0.0.1:1933" },
      logger,
      getClient,
      resolveAgentId,
      rememberSessionAgentId,
      queryConfigStore,
      traceRecorder,
      createContextEngine,
      setContextEngineRef,
    });

    expect(api.registerContextEngine).toHaveBeenCalledWith("openviking", expect.any(Function));
    expect(createContextEngine).not.toHaveBeenCalled();
    const registeredFactory = api.registerContextEngine.mock.calls[0]?.[1];

    expect(registeredFactory()).toBe(engine);
    expect(createContextEngine).toHaveBeenCalledWith({
      id: "openviking",
      name: "OpenViking",
      version: "0.1.0",
      cfg: { baseUrl: "http://127.0.0.1:1933" },
      logger,
      getClient,
      resolveAgentId,
      rememberSessionAgentId,
      queryConfigStore,
      traceRecorder,
    });
    expect(setContextEngineRef).toHaveBeenCalledWith(engine);
    expect(logger.info).toHaveBeenCalledWith(expect.stringContaining("registered context-engine"));
  });

  it("warns when the context-engine registration API is unavailable", () => {
    const logger = { info: vi.fn(), warn: vi.fn() };

    registerOpenVikingContextEngine({
      api: {},
      plugin: { id: "openviking", name: "OpenViking" },
      version: "0.1.0",
      cfg: {},
      logger,
      getClient: vi.fn(),
      resolveAgentId: vi.fn(),
      rememberSessionAgentId: vi.fn(),
      queryConfigStore: {},
      traceRecorder: {},
      createContextEngine: vi.fn(),
      setContextEngineRef: vi.fn(),
    });

    expect(logger.warn).toHaveBeenCalledWith(expect.stringContaining("registerContextEngine is unavailable"));
    expect(logger.info).not.toHaveBeenCalled();
  });

  it("registers externalized tool-result tools through a dedicated plugin module", async () => {
    const registerTool = vi.fn();
    const readToolResult = vi.fn().mockResolvedValue({
      content: "full tool result",
      offset: 0,
      limit: 20_000,
      total_chars: 16,
      has_more: false,
      tool_result_id: "tr_1",
      metadata: { tool_name: "ov_search" },
    });
    const deps = {
      registerTool,
      getClient: async () => ({ readToolResult }),
      resolvePluginSessionRouting: () => ({ agentId: "agent-main", ovSessionId: "ov-session-1" }),
      isBypassedSession: () => false,
      makeBypassedToolResult: (toolName: string) => ({ content: [{ type: "text" as const, text: `bypassed ${toolName}` }], details: { toolName } }),
      logger: { warn: vi.fn() },
    };

    registerOpenVikingToolResultTools(deps);

    expect(registerTool.mock.calls.map(([, opts]) => opts.name)).toEqual([
      "openviking_tool_result_read",
      "openviking_tool_result_search",
      "openviking_tool_result_list",
    ]);
    const readFactory = registerTool.mock.calls[0]?.[0];
    const readTool = readFactory({ sessionId: "session-1" });
    const result = await readTool.execute("call-1", {
      tool_output_ref: "viking://session/ov-session-1/tool-results/tr_1",
    });

    expect(readToolResult).toHaveBeenCalledWith(
      "ov-session-1",
      "tr_1",
      { offset: 0, limit: 20_000, includeMetadata: true },
      "agent-main",
    );
    expect(result.content[0].text).toBe("full tool result");
    expect(result.details).toMatchObject({ action: "read", tool_result_id: "tr_1" });
  });

  it("registers archive search and expansion through a dedicated plugin module", async () => {
    const registerTool = vi.fn();
    const recordAndFlush = vi.fn().mockResolvedValue(undefined);
    const grepSessionArchives = vi.fn().mockResolvedValue({
      count: 1,
      matches: [{
        uri: "viking://session/ov-session-1/history/archive_001#L12",
        line: 12,
        content: "important command output",
      }],
    });
    const getSessionArchive = vi.fn().mockResolvedValue({
      archive_id: "archive_001",
      abstract: "Compressed summary",
      messages: [{ id: "m1", role: "user", parts: [{ type: "text", text: "hello" }], created_at: "2026-06-10T00:00:00Z" }],
    });
    const deps = {
      registerTool,
      getClient: async () => ({ grepSessionArchives, getSessionArchive }),
      rememberSessionAgentId: vi.fn(),
      toOvSessionId: () => "ov-session-1",
      resolveAgentId: () => "agent-main",
      resolvePluginSessionRouting: () => ({
        agentId: "agent-main",
        sessionId: "session-1",
        ovSessionId: "ov-session-1",
      }),
      isBypassedSession: () => false,
      makeBypassedToolResult: (toolName: string) => ({ content: [{ type: "text" as const, text: `bypassed ${toolName}` }], details: { toolName } }),
      formatMessage: (message: { role: string; parts: Array<{ text?: string }> }) => `${message.role}: ${message.parts[0]?.text ?? ""}`,
      traceRecorder: { recordAndFlush },
      traceRecallMaxResultsPerSearch: 10,
      traceRecallPreviewChars: 20,
      createTraceId: () => "trace-archive-search",
      logger: { info: vi.fn(), warn: vi.fn() },
    };

    registerOpenVikingArchiveTools(deps);

    expect(registerTool.mock.calls.map(([, opts]) => opts.name)).toEqual([
      "ov_archive_search",
      "ov_archive_expand",
    ]);
    const searchFactory = registerTool.mock.calls[0]?.[0];
    const searchTool = searchFactory({ sessionId: "session-1", sessionKey: "session-key" });
    const searchResult = await searchTool.execute("call-1", { query: "command" });

    expect(grepSessionArchives).toHaveBeenCalledWith("ov-session-1", "command", {
      archiveId: undefined,
      caseInsensitive: true,
      agentId: "agent-main",
    });
    expect(searchResult.content[0].text).toContain("Found 1 match(es)");
    expect(searchResult.content[0].text).toContain("important command output");
    expect(searchResult.details).toMatchObject({ query: "command", matchCount: 1 });
    expect(recordAndFlush).toHaveBeenCalledWith(expect.objectContaining({
      traceId: "trace-archive-search",
      source: "ov_archive_search",
      operationType: "archive_grep",
    }));

    const expandFactory = registerTool.mock.calls[1]?.[0];
    const expandTool = expandFactory({ sessionId: "session-1" });
    const result = await expandTool.execute("call-2", { archiveId: "archive_001" });

    expect(getSessionArchive).toHaveBeenCalledWith("ov-session-1", "archive_001", "agent-main");
    expect(result.content[0].text).toContain("## archive_001");
    expect(result.content[0].text).toContain("user: hello");
    expect(result.details).toMatchObject({
      action: "expanded",
      archiveId: "archive_001",
      messageCount: 1,
      sessionId: "session-1",
      ovSessionId: "ov-session-1",
    });
  });

  it("registers import tools through a dedicated plugin module", async () => {
    const registerTool = vi.fn();
    const addResource = vi.fn().mockResolvedValue({ root_uri: "viking://resources/docs", warnings: ["warn"] });
    const addSkill = vi.fn().mockResolvedValue({ uri: "viking://user/skills/demo", name: "demo" });
    const deps = {
      registerTool,
      getClient: async () => ({ addResource, addSkill }),
      resolvePluginSessionRouting: () => ({ agentId: "agent-main" }),
      isBypassedSession: () => false,
      makeBypassedToolResult: (toolName: string) => ({ content: [{ type: "text" as const, text: `bypassed ${toolName}` }], details: { toolName } }),
      enableAddResourceTool: true,
    };

    registerOpenVikingImportTools(deps);

    expect(registerTool.mock.calls.map(([, opts]) => opts.name)).toEqual(["add_resource", "add_skill"]);
    const addResourceFactory = registerTool.mock.calls[0]?.[0];
    const addResourceTool = addResourceFactory({ sessionId: "session-1" });
    const resourceResult = await addResourceTool.execute("call-1", {
      source: "https://example.com/docs",
      to: "viking://resources/docs",
      wait: true,
      timeout: 30,
    });
    expect(addResource).toHaveBeenCalledWith({
      pathOrUrl: "https://example.com/docs",
      to: "viking://resources/docs",
      parent: undefined,
      reason: undefined,
      instruction: undefined,
      wait: true,
      timeout: 30,
    }, "agent-main");
    expect(resourceResult.content[0].text).toBe("Imported OpenViking resource. viking://resources/docs Warnings: warn");
    expect(resourceResult.details).toMatchObject({ action: "resource_imported", root_uri: "viking://resources/docs" });

    const addSkillFactory = registerTool.mock.calls[1]?.[0];
    const addSkillTool = addSkillFactory({ sessionId: "session-1" });
    const skillResult = await addSkillTool.execute("call-2", { data: "name: demo\n" });
    expect(addSkill).toHaveBeenCalledWith({
      path: undefined,
      data: "name: demo\n",
      wait: undefined,
      timeout: undefined,
    }, "agent-main");
    expect(skillResult.content[0].text).toBe("Imported OpenViking skill (demo). viking://user/skills/demo");
    expect(skillResult.details).toMatchObject({ action: "skill_imported", uri: "viking://user/skills/demo" });
  });

  it("keeps add_resource import tool behind explicit opt-in", () => {
    const registerTool = vi.fn();
    registerOpenVikingImportTools({
      registerTool,
      getClient: async () => ({ addResource: vi.fn(), addSkill: vi.fn() }),
      resolvePluginSessionRouting: () => ({ agentId: "agent-main" }),
      isBypassedSession: () => false,
      makeBypassedToolResult: (toolName: string) => ({ content: [{ type: "text" as const, text: `bypassed ${toolName}` }], details: { toolName } }),
      enableAddResourceTool: false,
    });

    expect(registerTool.mock.calls.map(([, opts]) => opts.name)).toEqual(["add_skill"]);
  });

  it("registers query tools through a dedicated plugin module", async () => {
    const registerTool = vi.fn();
    const searchOpenViking = vi.fn().mockResolvedValue({
      content: [{ type: "text" as const, text: "search result" }],
      details: { action: "searched", total: 1 },
    });
    const readOpenVikingContent = vi.fn().mockResolvedValue({
      content: [{ type: "text" as const, text: "read result" }],
      details: { action: "read", uri: "viking://resources/doc" },
    });
    const multiReadOpenVikingContent = vi.fn().mockResolvedValue({
      content: [{ type: "text" as const, text: "multi read result" }],
      details: { action: "multi_read", count: 2 },
    });
    const listOpenVikingDirectory = vi.fn().mockResolvedValue({
      content: [{ type: "text" as const, text: "list result" }],
      details: { action: "listed", count: 2 },
    });
    const deps = {
      registerTool,
      searchOpenViking,
      readOpenVikingContent,
      multiReadOpenVikingContent,
      listOpenVikingDirectory,
      resolvePluginSessionRouting: () => ({
        agentId: "agent-main",
        sessionId: "session-1",
        ovSessionId: "ov-session-1",
      }),
      isBypassedSession: () => false,
      makeBypassedToolResult: (toolName: string) => ({ content: [{ type: "text" as const, text: `bypassed ${toolName}` }], details: { toolName } }),
    };

    registerOpenVikingQueryTools(deps);

    expect(registerTool.mock.calls.map(([, opts]) => opts.name)).toEqual(["ov_search", "ov_read", "ov_multi_read", "ov_list"]);
    const searchFactory = registerTool.mock.calls[0]?.[0];
    const searchTool = searchFactory({ sessionId: "session-1" });
    const searched = await searchTool.execute("call-1", { query: "docs", uri: "viking://resources", limit: 3 });
    expect(searchOpenViking).toHaveBeenCalledWith({
      query: "docs",
      uri: "viking://resources",
      limit: 3,
    }, "agent-main", {
      agentId: "agent-main",
      sessionId: "session-1",
      ovSessionId: "ov-session-1",
    });
    expect(searched.details).toMatchObject({ action: "searched", total: 1 });

    const readFactory = registerTool.mock.calls[1]?.[0];
    const readTool = readFactory({ sessionId: "session-1" });
    const read = await readTool.execute("call-2", { uri: "viking://resources/doc" });
    expect(readOpenVikingContent).toHaveBeenCalledWith({ uri: "viking://resources/doc" }, "agent-main");
    expect(read.content[0].text).toBe("read result");

    const multiReadFactory = registerTool.mock.calls[2]?.[0];
    const multiReadTool = multiReadFactory({ sessionId: "session-1" });
    const multiRead = await multiReadTool.execute("call-3", { uris: ["viking://resources/a.md", "viking://resources/b.md"] });
    expect(multiReadOpenVikingContent).toHaveBeenCalledWith({
      uris: ["viking://resources/a.md", "viking://resources/b.md"],
    }, "agent-main");
    expect(multiRead.content[0].text).toBe("multi read result");

    const listFactory = registerTool.mock.calls[3]?.[0];
    const listTool = listFactory({ sessionId: "session-1" });
    const list = await listTool.execute("call-4", {
      uri: "viking://resources/doc",
      recursive: true,
      simple: true,
      limit: 5,
    });
    expect(listOpenVikingDirectory).toHaveBeenCalledWith({
      uri: "viking://resources/doc",
      recursive: true,
      simple: true,
      limit: 5,
    }, "agent-main");
    expect(list.content[0].text).toBe("list result");
  });

  it("registers memory store and forget through a dedicated plugin module", async () => {
    const registerTool = vi.fn();
    const addSessionMessage = vi.fn().mockResolvedValue(undefined);
    const commitSession = vi.fn().mockResolvedValue({
      session_id: "memory-store-temp",
      status: "completed",
      archived: false,
      memories_extracted: { core: 2 },
    });
    const deleteUri = vi.fn().mockResolvedValue(undefined);
    const find = vi.fn().mockResolvedValue({ memories: [], total: 0 });
    const deps = {
      registerTool,
      getClient: async () => ({ addSessionMessage, commitSession, deleteUri, find }),
      normalizeSessionId: (sessionId: string) => `normalized:${sessionId}`,
      createTempSessionId: () => "memory-store-temp",
      extractSenderId: () => "ou_01@abc",
      toRoleId: (senderId?: string) => senderId?.replace(/[^a-zA-Z0-9_-]/g, "_"),
      resolvePluginSessionRouting: () => ({ agentId: "agent-main" }),
      isBypassedSession: () => false,
      makeBypassedToolResult: (toolName: string) => ({ content: [{ type: "text" as const, text: `bypassed ${toolName}` }], details: { toolName } }),
      defaultTargetUri: "viking://user/default",
      defaultRecallScoreThreshold: 0.25,
      logFindRequests: true,
      logger: { info: vi.fn(), warn: vi.fn() },
    };

    registerOpenVikingMemoryTools(deps);

    expect(registerTool.mock.calls.map(([, opts]) => opts.name)).toEqual(["memory_store", "memory_forget"]);
    const storeFactory = registerTool.mock.calls[0]?.[0];
    const storeTool = storeFactory({ sessionId: "session-1", requesterSenderId: "ou_01@abc" });
    const stored = await storeTool.execute("call-1", { text: "remember this" });
    expect(addSessionMessage).toHaveBeenCalledWith(
      "memory-store-temp",
      "user",
      [{ type: "text", text: "remember this" }],
      "agent-main",
      undefined,
      "ou_01_abc",
    );
    expect(commitSession).toHaveBeenCalledWith("memory-store-temp", {
      wait: true,
      agentId: "agent-main",
      keepRecentCount: 0,
    });
    expect(stored.details).toMatchObject({ action: "stored", memoriesCount: 2, usedTempSession: true });

    const forgetFactory = registerTool.mock.calls[1]?.[0];
    const forgetTool = forgetFactory({ sessionId: "session-1" });
    const rejected = await forgetTool.execute("call-2", { uri: "viking://resources/r1" });
    expect(rejected.details).toMatchObject({ action: "rejected", uri: "viking://resources/r1" });
    expect(deleteUri).not.toHaveBeenCalled();

    const deleted = await forgetTool.execute("call-3", { uri: "viking://user/default/memories/m1" });
    expect(deleteUri).toHaveBeenCalledWith("viking://user/default/memories/m1", "agent-main");
    expect(deleted.content[0].text).toBe("Forgotten: viking://user/default/memories/m1");
    expect(deleted.details).toMatchObject({ action: "deleted", uri: "viking://user/default/memories/m1" });
  });

  it("registers recall trace tool through a dedicated plugin module", async () => {
    const registerTool = vi.fn();
    const traceResult = {
      entries: [{ traceId: "trace-1", source: "ov_search" }],
      lookupLayer: "memory",
      warnings: ["warn"],
    };
    const queryRecallTraces = vi.fn().mockResolvedValue(traceResult);
    const formatRecallTraceText = vi.fn().mockReturnValue("formatted trace");
    const deps = {
      registerTool,
      queryRecallTraces,
      formatRecallTraceText,
      resolvePluginSessionRouting: () => ({
        agentId: "agent-main",
        sessionId: "session-1",
        ovSessionId: "ov-session-1",
      }),
      isBypassedSession: () => false,
      makeBypassedToolResult: (toolName: string) => ({ content: [{ type: "text" as const, text: `bypassed ${toolName}` }], details: { toolName } }),
    };

    registerOpenVikingRecallTraceTools(deps);

    expect(registerTool.mock.calls.map(([, opts]) => opts.name)).toEqual(["ov_recall_trace"]);
    const traceFactory = registerTool.mock.calls[0]?.[0];
    const traceTool = traceFactory({ sessionId: "session-1" });
    const result = await traceTool.execute("call-1", { source: "ov_search", limit: 5 });

    expect(queryRecallTraces).toHaveBeenCalledWith({ source: "ov_search", limit: 5 }, {
      agentId: "agent-main",
      sessionId: "session-1",
      ovSessionId: "ov-session-1",
    });
    expect(formatRecallTraceText).toHaveBeenCalledWith(traceResult);
    expect(result.content[0].text).toBe("formatted trace");
    expect(result.details).toMatchObject({
      action: "queried",
      count: 1,
      lookupLayer: "memory",
      warnings: ["warn"],
      entries: traceResult.entries,
    });
  });

  it("registers memory recall tool through a dedicated plugin module", async () => {
    const registerTool = vi.fn();
    const memory = {
      uri: "viking://user/default/memories/m1",
      category: "preferences",
      abstract: "User prefers TDD.",
      score: 0.93,
      level: 2,
    };
    const find = vi.fn().mockResolvedValue({ memories: [memory], total: 1 });
    const read = vi.fn().mockResolvedValue("User prefers TDD.");
    const getDefaultAgentId = vi.fn().mockReturnValue("default-agent");
    const recordAndFlush = vi.fn().mockResolvedValue(undefined);
    const getEffective = vi.fn().mockResolvedValue({
      recallLimit: 1,
      scoreThreshold: 0.5,
      targetUri: "viking://user/default",
      resourceTypes: ["user"],
      candidateLimit: 4,
      maxInjectedChars: 500,
      rankingWeights: {},
      categoryWeights: {},
      resourceTypeWeights: {},
    });

    registerOpenVikingMemoryRecallTools({
      registerTool,
      getClient: async () => ({ find, read, getDefaultAgentId }),
      queryConfigStore: { getEffective },
      toQueryConfigContext: (session) => ({ agentId: session.agentId, sessionId: session.sessionId, sessionKey: session.sessionKey, ovSessionId: session.ovSessionId }),
      resolvePluginSessionRouting: () => ({
        agentId: "agent-main",
        sessionId: "session-1",
        sessionKey: "agent:agent-main:session-1",
        ovSessionId: "ov-session-1",
      }),
      isBypassedSession: () => false,
      makeBypassedToolResult: (toolName: string) => ({ content: [{ type: "text" as const, text: `bypassed ${toolName}` }], details: { toolName } }),
      resolveRecallSearchPlan: vi.fn(),
      postProcessMemories: (items) => items,
      pickMemoriesForInjection: (items) => items.slice(0, 1),
      buildMemoryLinesWithBudget: vi.fn(async (items, readFn) => {
        await readFn(items[0].uri);
        return { lines: ["1. [preferences] User prefers TDD. (93%)"], estimatedTokens: 8 };
      }),
      inferRecallResourceType: (uri) => uri.startsWith("viking://user/") ? "user" : "resource",
      createTraceId: () => "memory_recall-trace-1",
      boundTraceQuery: (query) => ({ query }),
      previewText: (value) => typeof value === "string" ? value : undefined,
      traceRecorder: { recordAndFlush },
      cfg: {
        recallTargetTypes: ["user"],
        traceRecallMaxResultsPerSearch: 5,
        traceRecallPreviewChars: 120,
        traceRecallQueryMaxChars: 200,
        logFindRequests: true,
      },
      logger: { info: vi.fn() },
    });

    expect(registerTool.mock.calls.map(([, opts]) => opts.name)).toEqual(["memory_recall"]);
    const recallFactory = registerTool.mock.calls[0]?.[0];
    const recallTool = recallFactory({ sessionId: "session-1" });
    const result = await recallTool.execute("call-1", { query: "TDD preference", limit: 2, scoreThreshold: 0.5 });

    expect(getEffective).toHaveBeenCalledWith({
      agentId: "agent-main",
      sessionId: "session-1",
      sessionKey: "agent:agent-main:session-1",
      ovSessionId: "ov-session-1",
    }, {
      recallLimit: 2,
      scoreThreshold: 0.5,
      targetUri: undefined,
      resourceTypes: undefined,
    });
    expect(find).toHaveBeenCalledWith("TDD preference", {
      targetUri: "viking://user/default",
      limit: 4,
      scoreThreshold: 0,
      actorPeerId: "agent-main",
    });
    expect(read).toHaveBeenCalledWith("viking://user/default/memories/m1", "agent-main");
    expect(recordAndFlush).toHaveBeenCalledWith(expect.objectContaining({
      traceId: "memory_recall-trace-1",
      sessionId: "session-1",
      sessionKey: "agent:agent-main:session-1",
      ovSessionId: "ov-session-1",
      agentId: "agent-main",
      source: "memory_recall",
      resourceTypes: ["user"],
      stats: { candidateCount: 1, selectedCount: 1, injectedCount: 1 },
    }));
    expect(result.content[0].text).toContain("Found 1 memories");
    expect(result.details).toMatchObject({
      count: 1,
      total: 1,
      scoreThreshold: 0.5,
      requestLimit: 4,
      recallMaxInjectedChars: 500,
    });
  });
});
