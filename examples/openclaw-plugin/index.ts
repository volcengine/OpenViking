import { Type } from "@sinclair/typebox";
import { memoryOpenVikingConfigSchema } from "./config.js";
import { registerSetupCli } from "./commands/setup.js";

import { OpenVikingClient, isMemoryUri } from "./client.js";
import type {
  AddResourceInput,
  AddSkillInput,
  FindResult,
  FindResultItem,
  CommitSessionResult,
  OVMessage,
} from "./client.js";
import {
  defaultMemoryPolicyForPeerRole,
  formatMessageFaithful,
  resolveMessagePeerId,
  resolveSearchPeerId,
  toPeerId,
} from "./context-engine.js";
import {
  getBoolFlag,
  getNumberFlag,
  getStringFlag,
  parseAddResourceCommandArgs,
  parseAddSkillCommandArgs,
  parseFlagArgs,
  parseOVSearchCommandArgs,
  type AddResourceToolInput,
  type AddSkillToolInput,
  type OVSearchInput,
} from "./command-args.js";
import {
  compileSessionPatterns,
  shouldBypassSession,
  extractNewTurnMessages,
} from "./text-utils.js";
import {
  clampScore,
  postProcessMemories,
  pickMemoriesForInjection,
} from "./memory-ranking.js";
import { withTimeout } from "./process-manager.js";
import {
  createMemoryOpenVikingContextEngine,
  openClawSessionToOvStorageId,
  openClawSessionRefToOvStorageId,
} from "./context-engine.js";
import type { ContextEngineWithCommit } from "./context-engine.js";
import {
  buildMemoryLines,
  buildMemoryLinesWithBudget,
  estimateTokenCount,
  prepareRecallQuery,
} from "./auto-recall.js";
import {
  createSessionAgentResolver,
  type SessionAgentLookup,
} from "./session-agent.js";
import {
  RecallTraceRecorder,
  normalizeResourceTypes,
  resolveRecallSearchPlan,
  type RecallResourceType,
  type RecallTraceEntry,
  type RecallTraceQuery,
  type RecallTraceResult,
  type RecallTraceSource,
} from "./recall-trace.js";
import {
  boundTraceQuery,
  createTraceId,
  inferRecallResourceType,
  previewText,
  toTraceSelectedItem,
  traceResultsFromFindResult,
} from "./recall-trace-utils.js";
import {
  formatOVListText,
  formatOVMultiReadText,
  formatOVReadText,
  formatOVSearchText,
  formatRecallTraceText,
  formatResourceImportText,
  formatSkillImportText,
  mergeFindResults,
} from "./tool-formatters.js";
export {
  parseAddResourceCommandArgs,
  parseAddSkillCommandArgs,
  parseOVSearchCommandArgs,
  tokenizeCommandArgs,
} from "./command-args.js";
export {
  buildMemoryLines,
  buildMemoryLinesWithBudget,
  estimateTokenCount,
  prepareRecallQuery,
};
export {
  createSessionAgentResolver,
  sanitizeRuntimeAgentId,
} from "./session-agent.js";
export type {
  SessionAgentResolveResult,
} from "./session-agent.js";
export {
  estimateAgentMessageTokens,
  estimateAgentMessagesTokens,
  estimateSerializedTokens,
  estimateTextTokens,
} from "./token-estimator.js";
export type {
  BuildMemoryLinesOptions,
  BuildMemoryLinesWithBudgetOptions,
  PreparedRecallQuery,
} from "./auto-recall.js";

type PluginLogger = {
  debug?: (message: string) => void;
  info: (message: string) => void;
  warn: (message: string) => void;
  error: (message: string) => void;
};

type HookAgentContext = {
  agentId?: string;
  sessionId?: string;
  sessionKey?: string;
};

type PluginSessionRouting = {
  sessionId?: string;
  sessionKey?: string;
  ovSessionId?: string;
  agentId: string;
};

type ToolDefinition = {
  name: string;
  label: string;
  description: string;
  parameters: unknown;
  execute: (_toolCallId: string, params: Record<string, unknown>) => Promise<unknown>;
};

type ToolContext = {
  sessionKey?: string;
  sessionId?: string;
  agentId?: string;
  senderId?: string;
};

type PluginCommandContext = {
  args?: string;
  commandBody: string;
  sessionKey?: string;
  sessionId?: string;
  agentId?: string;
  ovSessionId?: string;
};

type CommandResult = {
  text: string;
  details?: Record<string, unknown>;
};

type CommandDefinition = {
  name: string;
  description: string;
  acceptsArgs?: boolean;
  requireAuth?: boolean;
  handler: (ctx: PluginCommandContext) => CommandResult | Promise<CommandResult>;
};

type OVListInput = {
  uri: string;
  recursive?: boolean;
  simple?: boolean;
  limit?: number;
};

type OVReadInput = {
  uri: string;
};

type OVMultiReadInput = {
  uris: string[];
};

type RecallTraceToolInput = {
  turn?: "latest" | "all";
  traceId?: string;
  sessionId?: string;
  sessionKey?: string;
  ovSessionId?: string;
  source?: RecallTraceSource;
  resourceTypes?: RecallResourceType[] | string;
  since?: number;
  until?: number;
  includeContent?: boolean;
  limit?: number;
};

type ToolResultRef = {
  sessionId: string;
  toolResultId: string;
  ref: string;
};

function userSessionUri(sessionId: string): string {
  return `viking://user/sessions/${encodeURIComponent(sessionId)}`;
}

function toolResultRef(sessionId: string, toolResultId: string): string {
  return `${userSessionUri(sessionId)}/tool-results/${encodeURIComponent(toolResultId)}`;
}

function parseToolResultRef(value: unknown): ToolResultRef | null {
  const raw = typeof value === "string" ? value.trim() : "";
  if (!raw) {
    return null;
  }
  const match =
    raw.match(/^viking:\/\/user\/(?:(?:[^/]+)\/)?sessions\/([^/]+)\/tool-results\/([^/?#]+)(?:[?#].*)?$/) ??
    raw.match(/^viking:\/\/session\/([^/]+)\/tool-results\/([^/?#]+)(?:[?#].*)?$/);
  if (!match) {
    return null;
  }
  const sessionId = decodeURIComponent(match[1]!);
  const toolResultId = decodeURIComponent(match[2]!);
  if (!sessionId || !toolResultId) {
    return null;
  }
  return {
    sessionId,
    toolResultId,
    ref: toolResultRef(sessionId, toolResultId),
  };
}

function getOptionalInteger(value: unknown, fallback: number): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return fallback;
  }
  return Math.floor(value);
}

function getPositiveInteger(value: unknown, fallback: number): number {
  return Math.max(1, getOptionalInteger(value, fallback));
}

function textResult(text: string, details?: Record<string, unknown>) {
  const content = [{ type: "text" as const, text }];
  return details === undefined ? { content } : { content, details };
}

type OpenClawPluginApi = {
  pluginConfig?: unknown;
  logger: PluginLogger;
  registerTool: {
    (tool: ToolDefinition, opts?: { name?: string; names?: string[] }): void;
    (
      factory: (ctx: ToolContext) => ToolDefinition,
      opts?: { name?: string; names?: string[] },
    ): void;
  };
  registerCommand?: (command: CommandDefinition) => void;
  registerService: (service: {
    id: string;
    start: (ctx?: unknown) => void | Promise<void>;
    stop?: (ctx?: unknown) => void | Promise<void>;
  }) => void;
  registerContextEngine?: (id: string, factory: () => unknown) => void;
  registerCli?: (
    factory: (ctx: { program: unknown; workspaceDir?: string }) => void,
    opts?: { commands?: string[] },
  ) => void;
  on: (
    hookName: string,
    handler: (event: unknown, ctx?: HookAgentContext) => unknown,
    opts?: { priority?: number },
  ) => void;
};

type RecallTraceRouteAdapter = {
  registerRoute?: (route: {
    method: "GET";
    path: string;
    handler: (request?: {
      query?: Record<string, unknown>;
      params?: Record<string, unknown>;
      url?: string;
    }) => Promise<unknown>;
  }) => void;
};

function extractToolSenderId(ctx: unknown): string | undefined {
  if (!ctx || typeof ctx !== "object") {
    return undefined;
  }
  const toolCtx = ctx as Record<string, unknown>;
  if (typeof toolCtx.requesterSenderId === "string") {
    const trimmed = toolCtx.requesterSenderId.trim();
    if (trimmed) {
      return trimmed;
    }
  }
  if (typeof toolCtx.senderId === "string") {
    const trimmed = toolCtx.senderId.trim();
    if (trimmed) {
      return trimmed;
    }
  }
  return undefined;
}

function totalCommitMemories(r: CommitSessionResult): number {
  const m = r.memories_extracted;
  if (!m || typeof m !== "object") return 0;
  return Object.values(m).reduce((sum, n) => sum + (n ?? 0), 0);
}

const contextEnginePlugin = {
  id: "openviking",
  name: "Context Engine (OpenViking)",
  description: "OpenViking-backed context-engine memory with auto-recall/capture",
  kind: "context-engine" as const,
  configSchema: memoryOpenVikingConfigSchema,

  register(api: OpenClawPluginApi) {
    const rawCfg =
      api.pluginConfig && typeof api.pluginConfig === "object" && !Array.isArray(api.pluginConfig)
        ? (api.pluginConfig as Record<string, unknown>)
        : {};

    if (rawCfg.mode && rawCfg.mode !== "remote") {
      api.logger.warn(
        `openviking: legacy local mode detected (mode="${String(rawCfg.mode)}"). ` +
          "Migrating to remote mode. Please run 'openclaw openviking setup' to configure the remote server.",
      );
      rawCfg.mode = "remote";
      delete rawCfg.localBinaryPath;
      delete rawCfg.localDataDir;
      delete rawCfg.localPort;
      delete rawCfg.autoStart;
    }

    let cfg: ReturnType<typeof memoryOpenVikingConfigSchema.parse>;
    try {
      cfg = memoryOpenVikingConfigSchema.parse(rawCfg);
    } catch (parseErr) {
      api.logger.warn(
        `openviking: config parse failed (${parseErr instanceof Error ? parseErr.message : String(parseErr)}). ` +
          "Plugin loaded in setup-only mode. Run: openclaw openviking setup",
      );
      registerSetupCli(api);
      return;
    }

    const bypassSessionPatterns = compileSessionPatterns(cfg.bypassSessionPatterns);
    const rawPeerPrefix = rawCfg.peer_prefix;
    if (cfg.logFindRequests) {
      api.logger.info(
        "openviking: routing debug logging enabled (config logFindRequests, or env OPENVIKING_LOG_ROUTING=1 / OPENVIKING_DEBUG=1)",
      );
    }
    const verboseRoutingInfo = (message: string) => {
      if (cfg.logFindRequests) {
        api.logger.info(message);
      }
    };
    verboseRoutingInfo(
      `openviking: loaded plugin config peer_role="${cfg.peer_role}" peer_prefix="${cfg.peer_prefix}" ` +
        `(raw peer_prefix=${JSON.stringify(rawPeerPrefix ?? "(missing)")}; ` +
        `${
          cfg.peer_prefix
            ? 'non-empty → assistant peer_id is <peer_prefix>_<ctx.agentId> when peer_role="assistant", or <peer_prefix>_main when ctx.agentId is unknown'
            : 'empty → assistant peer_id follows OpenClaw ctx.agentId when peer_role="assistant", or "main" when ctx.agentId is unknown'
        })`,
    );
    const routingDebugLog = cfg.logFindRequests
      ? (msg: string) => {
          api.logger.info(msg);
        }
      : undefined;
    const defaultMemoryPolicy = defaultMemoryPolicyForPeerRole(cfg.peer_role);
    const tenantAccount = cfg.accountId;
    const tenantUser = cfg.userId;
    const enabledToolNames = new Set<string>(cfg.enabledTools);
    const registerOpenVikingTool = (
      toolOrFactory: ToolDefinition | ((ctx: ToolContext) => ToolDefinition),
      opts: { name: string; names?: string[] },
    ) => {
      const names = opts.names ?? [opts.name];
      if (!names.some((name) => enabledToolNames.has(name))) {
        api.logger.debug?.(`openviking: tool ${opts.name} disabled by config`);
        return;
      }
      if (typeof toolOrFactory === "function") {
        api.registerTool(toolOrFactory, opts);
      } else {
        api.registerTool(toolOrFactory, opts);
      }
    };

    const clientPromise = Promise.resolve(
      new OpenVikingClient(
        cfg.baseUrl,
        cfg.apiKey,
        cfg.peer_prefix,
        cfg.timeoutMs,
        tenantAccount,
        tenantUser,
        routingDebugLog,
      ),
    );

    const getClient = (): Promise<OpenVikingClient> => clientPromise;

    const traceRecorder = cfg.traceRecall
      ? new RecallTraceRecorder({
          memoryMaxEntries: cfg.traceRecallMaxEntries,
          persist: cfg.traceRecallPersist,
          traceDir: cfg.traceRecallDir,
          includeRawUserPreview: cfg.traceRecallIncludeRawUserPreview,
          retentionDays: cfg.traceRecallRetentionDays,
          queryMaxDays: cfg.traceRecallQueryMaxDays,
        })
      : undefined;

    const isBypassedSession = (ctx?: {
      sessionId?: string;
      sessionKey?: string;
    }): boolean => shouldBypassSession(ctx ?? {}, bypassSessionPatterns);

    const makeBypassedToolResult = (toolName: string) =>
      textResult(
        `OpenViking is bypassed for this session by bypassSessionPatterns; ${toolName} was skipped.`,
        {
          action: "bypassed",
          reason: "session_bypassed",
          toolName,
        },
      );

    const resolvePluginSessionRouting = (ctx?: SessionAgentLookup): PluginSessionRouting => {
      const sessionId = typeof ctx?.sessionId === "string" ? ctx.sessionId.trim() : "";
      const sessionKey = typeof ctx?.sessionKey === "string" ? ctx.sessionKey.trim() : "";
      let ovSessionId = typeof ctx?.ovSessionId === "string" ? ctx.ovSessionId.trim() : "";

      if (!ovSessionId && (sessionId || sessionKey)) {
        ovSessionId = openClawSessionToOvStorageId(
          sessionId || undefined,
          sessionKey || undefined,
        );
      }

      const session = {
        agentId: ctx?.agentId,
        sessionId: sessionId || undefined,
        sessionKey: sessionKey || undefined,
        ovSessionId: ovSessionId || undefined,
      };
      rememberSessionAgentId(session);

      return {
        sessionId: session.sessionId,
        sessionKey: session.sessionKey,
        ovSessionId: session.ovSessionId,
        agentId: resolveAgentId(session.sessionId, session.sessionKey, session.ovSessionId),
      };
    };

    const resolveToolSearchPeerId = (
      ctx: unknown,
      session: PluginSessionRouting,
    ): string | undefined =>
      resolveSearchPeerId({
        peerRole: cfg.peer_role,
        personPeerId: toPeerId(extractToolSenderId(ctx)),
        assistantPeerId: session.agentId,
      });

    const parseRecallTraceInput = (
      input: RecallTraceToolInput,
      ctx: { sessionId?: string; sessionKey?: string; ovSessionId?: string },
    ): RecallTraceQuery => ({
      turn: input.turn === "all" ? "all" : "latest",
      traceId: typeof input.traceId === "string" && input.traceId.trim() ? input.traceId.trim() : undefined,
      sessionId: typeof input.sessionId === "string" && input.sessionId.trim() ? input.sessionId.trim() : ctx.sessionId,
      sessionKey: typeof input.sessionKey === "string" && input.sessionKey.trim() ? input.sessionKey.trim() : undefined,
      ovSessionId: typeof input.ovSessionId === "string" && input.ovSessionId.trim() ? input.ovSessionId.trim() : ctx.ovSessionId,
      source: typeof input.source === "string" && input.source.trim() ? input.source as RecallTraceSource : undefined,
      resourceTypes: input.resourceTypes ? normalizeResourceTypes(input.resourceTypes) : undefined,
      since: typeof input.since === "number" ? input.since : undefined,
      until: typeof input.until === "number" ? input.until : undefined,
      limit: getPositiveInteger(input.limit, 20),
    });

    const shouldIncludeTraceContent = (input?: { includeContent?: boolean }): boolean =>
      input?.includeContent === true || cfg.traceRecallIncludeContentByDefault;

    const enrichTraceEntriesWithContent = async (
      result: { entries: RecallTraceEntry[]; lookupLayer: "memory" | "persistent"; warnings: string[] },
      includeContent: boolean,
      actorPeerId?: string,
    ): Promise<{ entries: RecallTraceEntry[]; lookupLayer: "memory" | "persistent"; warnings: string[] }> => {
      if (!includeContent || result.entries.length === 0) {
        return result;
      }
      const client = await getClient();
      const warnings = [...result.warnings];
      const entries = await Promise.all(result.entries.map(async (entry) => {
        const selected = await Promise.all(entry.selected.map(async (item) => {
          try {
            const content = await client.read(item.uri, actorPeerId);
            return {
              ...item,
              contentPreview: previewText(content, cfg.recallMaxContentChars),
            };
          } catch (err) {
            const readError = err instanceof Error ? err.message : String(err);
            warnings.push(`Failed to read recall trace content ${item.uri}: ${readError}`);
            return { ...item, readError };
          }
        }));
        return { ...entry, selected };
      }));
      return { ...result, entries, warnings };
    };

    const queryRecallTraces = async (
      input: RecallTraceToolInput,
      session: PluginSessionRouting,
      actorPeerId?: string,
    ): Promise<{ entries: RecallTraceEntry[]; lookupLayer: "memory" | "persistent"; warnings: string[] }> => {
      const base = traceRecorder
        ? await traceRecorder.queryWithFallback(parseRecallTraceInput(input, session))
        : { entries: [], lookupLayer: "memory" as const, warnings: ["traceRecall is disabled"] };
      return enrichTraceEntriesWithContent(base, shouldIncludeTraceContent(input), actorPeerId);
    };

    const registerRecallTraceRoutes = (ctx?: unknown): boolean => {
      const routeAdapter = ctx as RecallTraceRouteAdapter | undefined;
      if (typeof routeAdapter?.registerRoute !== "function") {
        return false;
      }
      const toQueryObject = (request?: {
        query?: Record<string, unknown>;
        params?: Record<string, unknown>;
        url?: string;
      }) => {
        const query: Record<string, unknown> = { ...(request?.query ?? {}) };
        if (request?.url) {
          const parsed = new URL(request.url, "http://openclaw.local");
          for (const [key, value] of parsed.searchParams.entries()) {
            query[key] = value;
          }
        }
        return { ...query, ...(request?.params ?? {}) };
      };
      const toBoolean = (value: unknown): boolean | undefined => {
        if (typeof value === "boolean") return value;
        if (typeof value !== "string") return undefined;
        return ["1", "true", "yes"].includes(value.trim().toLowerCase());
      };
      const toNumber = (value: unknown): number | undefined => {
        if (typeof value === "number") return value;
        if (typeof value === "string" && value.trim()) {
          const parsed = Number(value);
          return Number.isFinite(parsed) ? parsed : undefined;
        }
        return undefined;
      };
      const handle = async (request?: {
        query?: Record<string, unknown>;
        params?: Record<string, unknown>;
        url?: string;
      }) => {
        const query = toQueryObject(request);
        const session = resolvePluginSessionRouting(query as SessionAgentLookup);
        const result = await queryRecallTraces({
          turn: query.turn === "all" ? "all" : "latest",
          traceId: typeof query.traceId === "string" ? query.traceId : undefined,
          sessionId: typeof query.sessionId === "string" ? query.sessionId : undefined,
          sessionKey: typeof query.sessionKey === "string" ? query.sessionKey : undefined,
          ovSessionId: typeof query.ovSessionId === "string" ? query.ovSessionId : undefined,
          source: typeof query.source === "string" ? query.source as RecallTraceSource : undefined,
          resourceTypes: typeof query.resourceTypes === "string" ? query.resourceTypes : undefined,
          since: toNumber(query.since),
          until: toNumber(query.until),
          includeContent: toBoolean(query.includeContent),
          limit: toNumber(query.limit),
        }, session, resolveToolSearchPeerId(query, session));
        return { status: 200, body: { ok: true, ...result } };
      };
      routeAdapter.registerRoute({ method: "GET", path: "/api/openviking/recall-traces", handler: handle });
      routeAdapter.registerRoute({
        method: "GET",
        path: "/api/openviking/recall-traces/:traceId",
        handler: (request) => handle({
          ...request,
          query: {
            ...(request?.query ?? {}),
            traceId: typeof request?.params?.traceId === "string" ? request.params.traceId : undefined,
          },
        }),
      });
      return true;
    };

    const importResource = async (input: AddResourceInput, actorPeerId?: string) => {
      const client = await getClient();
      const result = await client.addResource(input, actorPeerId);
      return textResult(formatResourceImportText(result), {
        action: "resource_imported",
        ...result,
      });
    };

    const importSkill = async (input: AddSkillInput, actorPeerId?: string) => {
      const client = await getClient();
      const result = await client.addSkill(input, actorPeerId);
      return textResult(formatSkillImportText(result), {
        action: "skill_imported",
        ...result,
      });
    };

    const addResourceOpenViking = (input: AddResourceToolInput, actorPeerId?: string) =>
      importResource({
        pathOrUrl: input.source ?? "",
        to: input.to,
        parent: input.parent,
        reason: input.reason,
        instruction: input.instruction,
        wait: input.wait,
        timeout: input.timeout,
      }, actorPeerId);

    const addSkillOpenViking = (input: AddSkillToolInput, actorPeerId?: string) =>
      importSkill({
        path: input.source,
        data: input.data,
        wait: input.wait,
        timeout: input.timeout,
      }, actorPeerId);

    const searchOpenViking = async (
      input: OVSearchInput,
      traceCtx?: PluginSessionRouting,
      actorPeerId?: string,
    ) => {
      const query = input.query.trim();
      if (!query) {
        throw new Error("query is required");
      }
      const limit = Math.max(1, Math.floor(input.limit ?? 10));
      const client = await getClient();
      let result: FindResult;
      const searches: RecallTraceEntry["searches"] = [];
      if (input.uri) {
        const started = Date.now();
        result = await client.find(query, { targetUri: input.uri, limit, actorPeerId });
        const items = traceResultsFromFindResult(
          result,
          cfg.traceRecallPreviewChars,
          cfg.traceRecallMaxResultsPerSearch,
        );
        searches.push({
          resourceType: inferRecallResourceType(input.uri) ?? "resource",
          targetUriInput: input.uri,
          targetUriResolved: input.uri,
          limit,
          durationMs: Date.now() - started,
          total: result.total ?? items.length,
          results: items,
        });
      } else {
        const runSearch = async (
          targetUri: string | undefined,
          contextType: "resource" | "skill",
        ): Promise<FindResult> => {
          const resourceType: RecallResourceType = contextType === "skill" ? "user" : "resource";
          const started = Date.now();
          try {
            const found = await client.find(query, { targetUri, limit, contextType, actorPeerId });
            const items = traceResultsFromFindResult(
              found,
              cfg.traceRecallPreviewChars,
              cfg.traceRecallMaxResultsPerSearch,
            );
            searches.push({
              resourceType,
              contextType,
              targetUriResolved: targetUri,
              limit,
              durationMs: Date.now() - started,
              total: found.total ?? items.length,
              results: items,
            });
            return found;
          } catch (err) {
            searches.push({
              resourceType,
              contextType,
              targetUriResolved: targetUri,
              limit,
              durationMs: Date.now() - started,
              total: 0,
              results: [],
              error: err instanceof Error ? err.message : String(err),
            });
            throw err;
          }
        };
        const [resourcesSettled, skillsSettled] = await Promise.allSettled([
          runSearch(undefined, "resource"),
          runSearch(undefined, "skill"),
        ]);
        const successful: FindResult[] = [];
        if (resourcesSettled.status === "fulfilled") {
          successful.push(resourcesSettled.value);
        }
        if (skillsSettled.status === "fulfilled") {
          successful.push(skillsSettled.value);
        }
        if (successful.length === 0) {
          const firstError =
            resourcesSettled.status === "rejected"
              ? resourcesSettled.reason
              : skillsSettled.status === "rejected"
                ? skillsSettled.reason
                : "Both searches failed";
          throw firstError instanceof Error ? firstError : new Error(String(firstError));
        }
        if (resourcesSettled.status === "rejected") {
          api.logger.warn?.(`openviking: resource search failed: ${String(resourcesSettled.reason)}`);
        }
        if (skillsSettled.status === "rejected") {
          api.logger.warn?.(`openviking: skill search failed: ${String(skillsSettled.reason)}`);
        }
        result = mergeFindResults(successful);
      }
      const selected = [
        ...(result.memories ?? []),
        ...(result.resources ?? []),
        ...(result.skills ?? []),
      ].map((item) => toTraceSelectedItem(item, cfg.traceRecallPreviewChars, { displayed: true }));
      await traceRecorder?.recordAndFlush({
        schemaVersion: "1.0",
        traceId: createTraceId("ov_search"),
        ts: Date.now(),
        sessionId: traceCtx?.sessionId,
        sessionKey: traceCtx?.sessionKey,
        ovSessionId: traceCtx?.ovSessionId,
        agentId: traceCtx?.agentId,
        source: "ov_search",
        operationType: "semantic_find",
        resourceTypes: [...new Set(searches
          .map((search) => search.resourceType)
          .filter((resourceType): resourceType is RecallResourceType => resourceType !== "archive"))],
        trigger: boundTraceQuery(query, cfg.traceRecallQueryMaxChars),
        searches,
        selected,
        stats: {
          candidateCount: searches.reduce((sum, search) => sum + search.results.length, 0),
          selectedCount: selected.length,
          injectedCount: 0,
        },
      });
      return textResult(formatOVSearchText(query, input.uri, result), {
          action: "searched",
          query,
          uri: input.uri,
          peer_id: actorPeerId ?? null,
          memories: result.memories ?? [],
          resources: result.resources ?? [],
          skills: result.skills ?? [],
          total: result.total ?? 0,
      });
    };

    const readOpenViking = async (
      input: OVReadInput,
      actorPeerId?: string,
    ) => {
      const uri = input.uri.trim();
      if (!uri) {
        throw new Error("uri is required");
      }
      const client = await getClient();
      const content = await client.read(uri, actorPeerId);
      return textResult(formatOVReadText(uri, content), {
          action: "read",
          uri,
          chars: content.length,
      });
    };

    const multiReadOpenViking = async (
      input: OVMultiReadInput,
      actorPeerId?: string,
    ) => {
      const uris = input.uris
        .map((uri) => (typeof uri === "string" ? uri.trim() : ""))
        .filter((uri) => uri.length > 0);
      if (uris.length === 0) {
        throw new Error("uris is required");
      }
      const client = await getClient();
      const results = await Promise.all(
        uris.map(async (uri) => {
          try {
            const content = await client.read(uri, actorPeerId);
            return {
              uri,
              content,
              success: true,
              chars: content.length,
            };
          } catch (err) {
            const message = err instanceof Error ? err.message : String(err);
            return {
              uri,
              content: message,
              success: false,
              chars: 0,
            };
          }
        }),
      );
      return textResult(formatOVMultiReadText(results), {
          action: "multi_read",
          count: results.length,
          success_count: results.filter((result) => result.success).length,
          results,
      });
    };

    const listOpenViking = async (
      input: OVListInput,
      actorPeerId?: string,
    ) => {
      const uri = input.uri.trim();
      if (!uri) {
        throw new Error("uri is required");
      }
      const limit = Math.max(1, Math.floor(input.limit ?? 100));
      const client = await getClient();
      const entries = await client.list(
        uri,
        {
          recursive: input.recursive ?? false,
          simple: input.simple ?? false,
          nodeLimit: limit,
          actorPeerId,
        },
      );
      return textResult(formatOVListText(uri, entries), {
          action: "listed",
          uri,
          recursive: input.recursive ?? false,
          simple: input.simple ?? false,
          count: entries.length,
          entries,
      });
    };

    if (cfg.enableAddResourceTool) {
      registerOpenVikingTool(
        (ctx: ToolContext) => ({
          name: "add_resource",
          label: "Add Resource (OpenViking)",
          description:
            "Use only when the user explicitly asks to import, add, upload, save, or index a document, directory, URL, Git repository, or OpenClaw media attachment into OpenViking resources. " +
            "Never use this during search, retrieval, URI reading, or search-result optimization; use ov_search, ov_list, ov_read, and ov_multi_read for those flows. " +
            "For a '[media attached: /path ...]' document, set source to that exact local media path. " +
            "Set either to for an exact target URI or parent for a parent directory, never both. " +
            "Do not invent OpenViking upload REST endpoints.",
          parameters: Type.Object({
            source: Type.String({ description: "Local path, OpenClaw media attachment path, directory path, public URL, or Git URL" }),
            to: Type.Optional(Type.String({ description: "Exact target URI, e.g. viking://resources/project-docs. Mutually exclusive with parent." })),
            parent: Type.Optional(Type.String({ description: "Parent URI under viking://resources. Mutually exclusive with to." })),
            reason: Type.Optional(Type.String({ description: "Reason or note for adding this resource" })),
            instruction: Type.Optional(Type.String({ description: "Processing instruction for semantic extraction" })),
            wait: Type.Optional(Type.Boolean({ description: "Wait for processing to complete" })),
            timeout: Type.Optional(Type.Number({ description: "Timeout in seconds when wait is true" })),
          }),
          async execute(_toolCallId: string, params: Record<string, unknown>) {
            if (isBypassedSession(ctx)) {
              return makeBypassedToolResult("add_resource");
            }
            const session = resolvePluginSessionRouting(ctx);
            return addResourceOpenViking({
              source: typeof params.source === "string" ? params.source : undefined,
              to: typeof params.to === "string" ? params.to : undefined,
              parent: typeof params.parent === "string" ? params.parent : undefined,
              reason: typeof params.reason === "string" ? params.reason : undefined,
              instruction: typeof params.instruction === "string" ? params.instruction : undefined,
              wait: typeof params.wait === "boolean" ? params.wait : undefined,
              timeout: typeof params.timeout === "number" ? params.timeout : undefined,
            }, resolveToolSearchPeerId(ctx, session));
          },
        }),
        { name: "add_resource" },
      );
    }

    registerOpenVikingTool(
      (ctx: ToolContext) => ({
        name: "add_skill",
        label: "Add Skill (OpenViking)",
        description:
          "Use only when the user explicitly asks to import, add, install, or register a skill into OpenViking. " +
          "Set source to a local SKILL.md file or skill directory, or data to raw SKILL.md content or an MCP tool dict.",
        parameters: Type.Object({
          source: Type.Optional(Type.String({ description: "Local SKILL.md path or skill directory path" })),
          data: Type.Optional(Type.Any({ description: "Raw SKILL.md content or MCP tool dict" })),
          wait: Type.Optional(Type.Boolean({ description: "Wait for processing to complete" })),
          timeout: Type.Optional(Type.Number({ description: "Timeout in seconds when wait is true" })),
        }),
        async execute(_toolCallId: string, params: Record<string, unknown>) {
          if (isBypassedSession(ctx)) {
            return makeBypassedToolResult("add_skill");
          }
          const session = resolvePluginSessionRouting(ctx);
          return addSkillOpenViking(
            {
              source: typeof params.source === "string" ? params.source : undefined,
              data: params.data,
              wait: typeof params.wait === "boolean" ? params.wait : undefined,
              timeout: typeof params.timeout === "number" ? params.timeout : undefined,
            },
            resolveToolSearchPeerId(ctx, session),
          );
        },
      }),
      { name: "add_skill" },
    );

    registerOpenVikingTool(
      (ctx: ToolContext) => ({
        name: "ov_search",
        label: "Search (OpenViking)",
        description:
          "Search OpenViking resources and skills. Use after importing, or when the user asks to search OpenViking resources or skills. " +
          "Search only returns ranked snippets; call ov_read on exact hit URIs before answering precise questions. " +
          "When a result is part of a split document or a multi-step procedure, call ov_list on the parent URI to inspect sibling chunks and overview files before answering.",
        parameters: Type.Object({
          query: Type.String({ description: "Search query" }),
          uri: Type.Optional(Type.String({ description: "Optional search URI. Defaults to all resource and skill contexts." })),
          limit: Type.Optional(Type.Number({ description: "Max results per search scope. Default: 10" })),
        }),
        async execute(_toolCallId: string, params: Record<string, unknown>) {
          if (isBypassedSession(ctx)) {
            return makeBypassedToolResult("ov_search");
          }
          const session = resolvePluginSessionRouting(ctx);
          const peerId = resolveToolSearchPeerId(ctx, session);
          return searchOpenViking({
            query: String((params as { query?: unknown }).query ?? ""),
            uri: typeof params.uri === "string" ? params.uri : undefined,
            limit: typeof params.limit === "number" ? params.limit : undefined,
          }, session, peerId);
        },
      }),
      { name: "ov_search" },
    );

    registerOpenVikingTool(
      (ctx: ToolContext) => ({
        name: "ov_read",
        label: "Read (OpenViking)",
        description:
          "Read the full original content of one exact OpenViking URI returned by ov_search or ov_list. " +
          "Use after ov_search before answering precise documentation, codebase, configuration, or procedural questions. " +
          "Do not use filesystem read/cat for viking:// URIs.",
        parameters: Type.Object({
          uri: Type.String({ description: "Exact OpenViking URI to read, e.g. viking://resources/project/docs/step-1.md" }),
        }),
        async execute(_toolCallId: string, params: Record<string, unknown>) {
          if (isBypassedSession(ctx)) {
            return makeBypassedToolResult("ov_read");
          }
          const session = resolvePluginSessionRouting(ctx);
          const actorPeerId = resolveToolSearchPeerId(ctx, session);
          return readOpenViking({
            uri: String((params as { uri?: unknown }).uri ?? ""),
          }, actorPeerId);
        },
      }),
      { name: "ov_read" },
    );

    registerOpenVikingTool(
      (ctx: ToolContext) => ({
        name: "ov_multi_read",
        label: "Multi Read (OpenViking)",
        description:
          "Read the full original content of multiple exact OpenViking URIs concurrently. " +
          "Use after ov_search and ov_list to read an overview plus sibling chunks for split documents or multi-step procedures.",
        parameters: Type.Object({
          uris: Type.Array(Type.String({ description: "Exact OpenViking URI to read" }), {
            description: "Exact OpenViking URIs to read",
          }),
        }),
        async execute(_toolCallId: string, params: Record<string, unknown>) {
          if (isBypassedSession(ctx)) {
            return makeBypassedToolResult("ov_multi_read");
          }
          const session = resolvePluginSessionRouting(ctx);
          const actorPeerId = resolveToolSearchPeerId(ctx, session);
          const uris = Array.isArray((params as { uris?: unknown }).uris)
            ? (params as { uris: unknown[] }).uris.map((uri) => String(uri))
            : [];
          return multiReadOpenViking({ uris }, actorPeerId);
        },
      }),
      { name: "ov_multi_read" },
    );

    registerOpenVikingTool(
      (ctx: ToolContext) => ({
        name: "ov_list",
        label: "List (OpenViking)",
        description:
          "List files and directories under an OpenViking URI. Use after ov_search to inspect a hit's parent directory, sibling chunks, or .overview.md files when search only returns ranked snippets.",
        parameters: Type.Object({
          uri: Type.String({ description: "OpenViking directory URI to list, e.g. viking://resources/project/docs" }),
          recursive: Type.Optional(Type.Boolean({ description: "List nested entries recursively. Default: false" })),
          simple: Type.Optional(Type.Boolean({ description: "Return only URI entries from OpenViking. Default: false" })),
          limit: Type.Optional(Type.Number({ description: "Maximum entries to list. Default: 100" })),
        }),
        async execute(_toolCallId: string, params: Record<string, unknown>) {
          if (isBypassedSession(ctx)) {
            return makeBypassedToolResult("ov_list");
          }
          const session = resolvePluginSessionRouting(ctx);
          const actorPeerId = resolveToolSearchPeerId(ctx, session);
          return listOpenViking({
            uri: String((params as { uri?: unknown }).uri ?? ""),
            recursive: typeof params.recursive === "boolean" ? params.recursive : undefined,
            simple: typeof params.simple === "boolean" ? params.simple : undefined,
            limit: typeof params.limit === "number" ? params.limit : undefined,
          }, actorPeerId);
        },
      }),
      { name: "ov_list" },
    );

    api.registerCommand?.({
      name: "add-resource",
      description: "Add a resource into OpenViking.",
      acceptsArgs: true,
      handler: async (ctx: PluginCommandContext) => {
        try {
          if (isBypassedSession(ctx)) {
            const bypassed = makeBypassedToolResult("add_resource");
            return { text: bypassed.content[0]!.text, details: bypassed.details };
          }
          const session = resolvePluginSessionRouting(ctx);
          const input = parseAddResourceCommandArgs(ctx.args ?? "");
          const result = await addResourceOpenViking(input, resolveToolSearchPeerId(ctx, session));
          return { text: result.content[0]!.text, details: result.details };
        } catch (err) {
          return { text: `OpenViking add resource failed: ${err instanceof Error ? err.message : String(err)}` };
        }
      },
    });

    api.registerCommand?.({
      name: "add-skill",
      description: "Add a skill into OpenViking.",
      acceptsArgs: true,
      handler: async (ctx: PluginCommandContext) => {
        try {
          if (isBypassedSession(ctx)) {
            const bypassed = makeBypassedToolResult("add_skill");
            return { text: bypassed.content[0]!.text, details: bypassed.details };
          }
          const session = resolvePluginSessionRouting(ctx);
          const input = parseAddSkillCommandArgs(ctx.args ?? "");
          const result = await addSkillOpenViking(input, resolveToolSearchPeerId(ctx, session));
          return { text: result.content[0]!.text, details: result.details };
        } catch (err) {
          return { text: `OpenViking add skill failed: ${err instanceof Error ? err.message : String(err)}` };
        }
      },
    });

    api.registerCommand?.({
      name: "ov-search",
      description: "Search OpenViking resources and skills.",
      acceptsArgs: true,
      handler: async (ctx: PluginCommandContext) => {
        try {
          if (isBypassedSession(ctx)) {
            const bypassed = makeBypassedToolResult("ov_search");
            return { text: bypassed.content[0]!.text, details: bypassed.details };
          }
          const session = resolvePluginSessionRouting(ctx);
          const input = parseOVSearchCommandArgs(ctx.args ?? "");
          const peerId = resolveToolSearchPeerId(ctx, session);
          const result = await searchOpenViking(input, session, peerId);
          return { text: result.content[0]!.text, details: result.details };
        } catch (err) {
          return { text: `OpenViking search failed: ${err instanceof Error ? err.message : String(err)}` };
        }
      },
    });

    registerOpenVikingTool(
      (ctx: ToolContext) => ({
        name: "memory_recall",
        label: "Memory Recall (OpenViking)",
        description:
          "Search long-term memories from OpenViking. Use when you need past user preferences, facts, or decisions.",
        parameters: Type.Object({
          query: Type.String({ description: "Search query" }),
          limit: Type.Optional(
            Type.Number({ description: "Max results (default: plugin config)" }),
          ),
          scoreThreshold: Type.Optional(
            Type.Number({ description: "Minimum score (0-1, default: plugin config)" }),
          ),
          targetUri: Type.Optional(
            Type.String({ description: "Search scope URI (default: plugin config)" }),
          ),
          resourceTypes: Type.Optional(
            Type.Array(Type.String({ description: "resource, user, or agent; used when targetUri is omitted. Use ov_archive_search/ov_archive_expand for session history." })),
          ),
        }),
        async execute(_toolCallId: string, params: Record<string, unknown>) {
          if (isBypassedSession(ctx)) {
            return makeBypassedToolResult("memory_recall");
          }
          const session = resolvePluginSessionRouting(ctx);
          const peerId = resolveToolSearchPeerId(ctx, session);
          const { query } = params as { query: string };
          const limit =
            typeof (params as { limit?: number }).limit === "number"
              ? Math.max(1, Math.floor((params as { limit: number }).limit))
              : cfg.recallLimit;
          const scoreThreshold =
            typeof (params as { scoreThreshold?: number }).scoreThreshold === "number"
              ? Math.max(0, Math.min(1, (params as { scoreThreshold: number }).scoreThreshold))
              : cfg.recallScoreThreshold;
          const targetUri =
            typeof (params as { targetUri?: string }).targetUri === "string"
              ? (params as { targetUri: string }).targetUri
              : undefined;
          const requestedResourceTypes = Object.prototype.hasOwnProperty.call(params, "resourceTypes")
            ? (params as { resourceTypes?: unknown }).resourceTypes
            : undefined;
          const requestLimit = Math.max(limit * 4, 20);

          const recallClient = await getClient();
          if (cfg.logFindRequests) {
            api.logger.info(
              `openviking: memory_recall assistant_peer_id="${session.agentId}" ` +
                `peer_id="${peerId ?? ""}" ` +
                `(plugin defaultAgentId="${recallClient.getDefaultAgentId()}" is not sent as an OpenViking agent identity)`,
            );
          }

          let result: FindResult;
          let memoryRecallSearches: RecallTraceEntry["searches"] = [];
          let requestedTraceResourceTypes: RecallResourceType[] | undefined;
          if (targetUri) {
            // 如果指定了目标 URI，只检索该位置
            const started = Date.now();
            result = await recallClient.find(
              query,
              {
                targetUri,
                limit: requestLimit,
                scoreThreshold: 0,
                actorPeerId: peerId,
              },
            );
            const traceResults = traceResultsFromFindResult(
              result,
              cfg.traceRecallPreviewChars,
              cfg.traceRecallMaxResultsPerSearch,
            );
            memoryRecallSearches = [{
              resourceType: inferRecallResourceType(targetUri) ?? "resource",
              targetUriInput: targetUri,
              targetUriResolved: targetUri,
              limit: requestLimit,
              scoreThreshold,
              durationMs: Date.now() - started,
              total: result.total ?? traceResults.length,
              results: traceResults,
            }];
          } else {
            const searchPlan = resolveRecallSearchPlan(requestedResourceTypes ?? cfg.recallTargetTypes, {
              ovSessionId: session.ovSessionId,
              agentId: session.agentId,
            });
            requestedTraceResourceTypes = searchPlan.resourceTypes;
            memoryRecallSearches.push(...searchPlan.skipped.map((skipped) => ({
              resourceType: skipped.resourceType,
              limit: requestLimit,
              scoreThreshold,
              durationMs: 0,
              total: 0,
              results: [],
              error: skipped.reason,
            })));
            const searchPromises = searchPlan.searches.map((search) =>
              recallClient.find(
                query,
                {
                  targetUri: search.targetUri,
                  limit: requestLimit,
                  scoreThreshold: 0,
                  contextType: search.contextType,
                  actorPeerId: peerId,
                },
              ),
            );
            const settled = await Promise.allSettled(searchPromises);
            const allMemories: FindResultItem[] = [];
            for (let index = 0; index < settled.length; index += 1) {
              const s = settled[index]!;
              const search = searchPlan.searches[index]!;
              if (s.status === "fulfilled") {
                allMemories.push(...(s.value.memories ?? []), ...(s.value.resources ?? []));
                const traceResults = traceResultsFromFindResult(
                  s.value,
                  cfg.traceRecallPreviewChars,
                  cfg.traceRecallMaxResultsPerSearch,
                );
                memoryRecallSearches.push({
                  resourceType: search.resourceType,
                  contextType: search.contextType,
                  targetUriInput: search.targetUri,
                  targetUriResolved: search.targetUri,
                  limit: requestLimit,
                  scoreThreshold,
                  durationMs: 0,
                  total: s.value.total ?? traceResults.length,
                  results: traceResults,
                });
              } else {
                memoryRecallSearches.push({
                  resourceType: search.resourceType,
                  contextType: search.contextType,
                  targetUriInput: search.targetUri,
                  targetUriResolved: search.targetUri,
                  limit: requestLimit,
                  scoreThreshold,
                  durationMs: 0,
                  total: 0,
                  results: [],
                  error: s.reason instanceof Error ? s.reason.message : String(s.reason),
                });
              }
            }
            const uniqueMemories = allMemories.filter((memory, index, self) =>
              index === self.findIndex((m) => m.uri === memory.uri)
            );
            const leafOnly = uniqueMemories.filter((m) => !m.level || m.level === 2);
            result = {
              memories: leafOnly,
              total: leafOnly.length,
            };
          }

          const leafOnly = (result.memories ?? []).filter((m) => !m.level || m.level === 2);
          const processed = postProcessMemories(leafOnly, {
            limit: requestLimit,
            scoreThreshold,
          });
          const memories = pickMemoriesForInjection(processed, limit, query);
          const candidateTraceResults = traceResultsFromFindResult(
            { memories: leafOnly },
            cfg.traceRecallPreviewChars,
            cfg.traceRecallMaxResultsPerSearch,
          );
          const traceResourceTypes = [...new Set(
            (requestedTraceResourceTypes ?? (targetUri ? [inferRecallResourceType(targetUri)] : memoryRecallSearches.map((search) => search.resourceType)))
              .filter((resourceType): resourceType is RecallResourceType => Boolean(resourceType) && resourceType !== "archive"),
          )];
          const recordMemoryRecallTrace = async (injectedUris: Set<string>) => {
            await traceRecorder?.recordAndFlush({
              schemaVersion: "1.0",
              traceId: createTraceId("memory_recall"),
              ts: Date.now(),
              sessionId: session.sessionId,
              sessionKey: session.sessionKey,
              ovSessionId: session.ovSessionId,
              agentId: session.agentId,
              source: "memory_recall",
              operationType: "semantic_find",
              resourceTypes: traceResourceTypes.length > 0 ? traceResourceTypes : ["user"],
              trigger: boundTraceQuery(query, cfg.traceRecallQueryMaxChars),
              searches: memoryRecallSearches.length > 0 ? memoryRecallSearches : [{
                resourceType: inferRecallResourceType(targetUri) ?? "user",
                contextType: targetUri ? undefined : "memory",
                targetUriInput: targetUri,
                targetUriResolved: targetUri,
                limit: requestLimit,
                scoreThreshold,
                durationMs: 0,
                total: result.total ?? leafOnly.length,
                results: candidateTraceResults,
              }],
              selected: memories.map((item) => toTraceSelectedItem(item, cfg.traceRecallPreviewChars, {
                injected: injectedUris.has(item.uri),
                displayed: injectedUris.has(item.uri),
              })),
              stats: {
                candidateCount: leafOnly.length,
                selectedCount: memories.length,
                injectedCount: injectedUris.size,
              },
            });
          };
          if (memories.length === 0) {
            await recordMemoryRecallTrace(new Set());
            return textResult("No relevant OpenViking memories found.", { count: 0, total: result.total ?? 0, scoreThreshold });
          }
          const { lines: memoryLines } = await buildMemoryLinesWithBudget(
            memories,
            (uri) => recallClient.read(uri, peerId),
            {
              recallPreferAbstract: false,
              recallMaxInjectedChars: cfg.recallMaxInjectedChars,
            },
          );
          if (memoryLines.length === 0) {
            await recordMemoryRecallTrace(new Set());
            return textResult(`No complete OpenViking memories fit recallMaxInjectedChars=${cfg.recallMaxInjectedChars}.`, {
                count: 0,
                memories,
                total: result.total ?? memories.length,
                scoreThreshold,
                requestLimit,
                recallMaxInjectedChars: cfg.recallMaxInjectedChars,
            });
          }
          await recordMemoryRecallTrace(new Set(memories.slice(0, memoryLines.length).map((item) => item.uri)));
          return textResult(`Found ${memoryLines.length} memories:\n\n${memoryLines.join("\n")}`, {
              count: memoryLines.length,
              memories,
              total: result.total ?? memories.length,
              scoreThreshold,
              requestLimit,
              recallMaxInjectedChars: cfg.recallMaxInjectedChars,
          });
        },
      }),
      { name: "memory_recall" },
    );

    registerOpenVikingTool(
      (ctx: ToolContext) => ({
        name: "ov_recall_trace",
        label: "Recall Trace (OpenViking)",
        description: "Query OpenViking recall trace records captured by auto-recall and explicit recall/search tools.",
        parameters: Type.Object({
          turn: Type.Optional(Type.String({ description: "latest or all (default: latest)" })),
          traceId: Type.Optional(Type.String({ description: "Exact trace id" })),
          sessionId: Type.Optional(Type.String({ description: "OpenClaw session id" })),
          sessionKey: Type.Optional(Type.String({ description: "OpenClaw session key" })),
          ovSessionId: Type.Optional(Type.String({ description: "OpenViking session id" })),
          source: Type.Optional(Type.String({ description: "auto_recall, memory_recall, ov_search, or ov_archive_search" })),
          resourceTypes: Type.Optional(Type.Array(Type.String({ description: "resource, user, or agent" }))),
          since: Type.Optional(Type.Number({ description: "Unix timestamp lower bound in milliseconds" })),
          until: Type.Optional(Type.Number({ description: "Unix timestamp upper bound in milliseconds" })),
          includeContent: Type.Optional(Type.Boolean({ description: "Read selected/displayed URI content previews on demand" })),
          limit: Type.Optional(Type.Number({ description: "Maximum traces to return (default: 20)" })),
        }),
        async execute(_toolCallId: string, params: Record<string, unknown>) {
          if (isBypassedSession(ctx)) {
            return makeBypassedToolResult("ov_recall_trace");
          }
          const session = resolvePluginSessionRouting(ctx);
          const result = await queryRecallTraces(
            params as RecallTraceToolInput,
            session,
            resolveToolSearchPeerId(ctx, session),
          );
          return textResult(formatRecallTraceText(result), {
              action: "queried",
              count: result.entries.length,
              lookupLayer: result.lookupLayer,
              warnings: result.warnings,
              entries: result.entries,
          });
        },
      }),
      { name: "ov_recall_trace" },
    );

    api.registerCommand?.({
      name: "ov-recall-trace",
      description: "Query OpenViking recall trace records.",
      acceptsArgs: true,
      handler: async (ctx: PluginCommandContext) => {
        try {
          if (isBypassedSession(ctx)) {
            const bypassed = makeBypassedToolResult("ov_recall_trace");
            return { text: bypassed.content[0]!.text, details: bypassed.details };
          }
          const session = resolvePluginSessionRouting(ctx);
          const flags = parseFlagArgs(ctx.args ?? "").flags;
          const input: RecallTraceToolInput = {
            turn: getStringFlag(flags, "turn") as "latest" | "all" | undefined,
            traceId: getStringFlag(flags, "trace-id"),
            sessionId: getStringFlag(flags, "session-id"),
            sessionKey: getStringFlag(flags, "session-key"),
            ovSessionId: getStringFlag(flags, "ov-session-id"),
            source: getStringFlag(flags, "source") as RecallTraceSource | undefined,
            resourceTypes: getStringFlag(flags, "resource-types"),
            since: getNumberFlag(flags, "since"),
            until: getNumberFlag(flags, "until"),
            includeContent: getBoolFlag(flags, "include-content"),
            limit: getNumberFlag(flags, "limit"),
          };
          const result = await queryRecallTraces(input, session, resolveToolSearchPeerId(ctx, session));
          return {
            text: formatRecallTraceText(result),
            details: {
              count: result.entries.length,
              lookupLayer: result.lookupLayer,
              warnings: result.warnings,
              entries: result.entries,
            },
          };
        } catch (err) {
          return { text: `OpenViking recall trace query failed: ${err instanceof Error ? err.message : String(err)}` };
        }
      },
    });

    registerOpenVikingTool(
      (ctx: ToolContext) => ({
        name: "memory_store",
        label: "Memory Store (OpenViking)",
        description:
          "Store text in OpenViking memory pipeline by writing to a session and running memory extraction. Use when the user explicitly asks to remember, save, or store an important long-term fact, preference, project, or decision; automatic capture is threshold/commit dependent.",
        parameters: Type.Object({
          text: Type.String({ description: "Information to store as memory source text" }),
          role: Type.Optional(Type.String({ description: "Session role, default user" })),
          sessionId: Type.Optional(Type.String({ description: "Existing OpenViking session ID" })),
        }),
        async execute(_toolCallId: string, params: Record<string, unknown>) {
          if (isBypassedSession(ctx)) {
            return makeBypassedToolResult("memory_store");
          }
          const session = resolvePluginSessionRouting(ctx);
          const { text } = params as { text: string };
          const role =
            typeof (params as { role?: string }).role === "string"
              ? (params as { role: string }).role
              : "user";
          const explicitSessionId =
            typeof (params as { sessionId?: unknown }).sessionId === "string" &&
              (params as { sessionId: string }).sessionId.trim()
              ? openClawSessionRefToOvStorageId((params as { sessionId: string }).sessionId)
              : undefined;

          if (cfg.logFindRequests) {
            api.logger.info?.(
              `openviking: memory_store invoked (textLength=${text?.length ?? 0}, sessionId=${explicitSessionId ?? "auto"})`,
            );
          }

          let sessionId = explicitSessionId;
          let usedTempSession = false;
          try {
            const c = await getClient();
            if (!sessionId) {
              sessionId = `memory-store-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
              usedTempSession = true;
            }
            const peerId = resolveMessagePeerId({
              peerRole: cfg.peer_role,
              role,
              personPeerId: toPeerId(extractToolSenderId(ctx)),
              assistantPeerId: session.agentId,
            });
            if (defaultMemoryPolicy) {
              await c.ensureSession(
                sessionId,
                { memoryPolicy: defaultMemoryPolicy },
              );
            }
            await c.addSessionMessage(
              sessionId,
              role,
              [{ type: "text" as const, text }],
              undefined,
              peerId,
            );
            const commitResult = await c.commitSession(sessionId, {
              wait: true,
              keepRecentCount: 0,
            });
            const memoriesCount = totalCommitMemories(commitResult);
            if (commitResult.status === "failed") {
              api.logger.warn(
                `openviking: memory_store commit failed (sessionId=${sessionId}): ${commitResult.error ?? "unknown"}`,
              );
              return textResult(`Memory extraction failed for session ${sessionId}: ${commitResult.error ?? "unknown"}`, {
                action: "failed",
                sessionId,
                status: "failed",
                error: commitResult.error,
                usedTempSession,
              });
            }
            if (commitResult.status === "timeout") {
              api.logger.warn(
                `openviking: memory_store commit timed out (sessionId=${sessionId}), task_id=${commitResult.task_id ?? "none"}. Memories may still be extracting in background.`,
              );
              return textResult(`Memory extraction timed out for session ${sessionId}. It may still complete in the background (task_id=${commitResult.task_id ?? "none"}).`, {
                action: "timeout",
                sessionId,
                status: "timeout",
                taskId: commitResult.task_id,
                usedTempSession,
              });
            }
            if (memoriesCount === 0) {
              api.logger.warn(
                `openviking: memory_store committed but 0 memories extracted (sessionId=${sessionId}). ` +
                  "No OpenViking-managed long-term memory was created. Check memory.extraction_enabled, VLM configuration/API keys, or whether the text was judged not durable.",
              );
              return textResult(
                `Memory extraction completed for session ${sessionId}, but produced 0 memories. ` +
                  "No OpenViking-managed long-term memory was stored. Check memory.extraction_enabled, VLM configuration/API keys, or whether the text is durable enough to remember.",
                {
                  action: "failed",
                  sessionId,
                  status: commitResult.status,
                  error: "no_memories_extracted",
                  memoriesCount,
                  archived: commitResult.archived ?? false,
                  usedTempSession,
                },
              );
            } else {
              api.logger.info?.(`openviking: memory_store committed, memories=${memoriesCount}`);
            }
            return textResult(`Stored in OpenViking session ${sessionId} and committed ${memoriesCount} memories.`, {
                action: "stored",
                sessionId,
                memoriesCount,
                status: commitResult.status,
                archived: commitResult.archived ?? false,
                usedTempSession,
            });
          } catch (err) {
            api.logger.warn(`openviking: memory_store failed: ${String(err)}`);
            throw err;
          }
        },
      }),
      { name: "memory_store" },
    );

    registerOpenVikingTool(
      (ctx: ToolContext) => ({
        name: "memory_forget",
        label: "Memory Forget (OpenViking)",
        description:
          "Forget memory by URI, or search then delete when a strong single match is found.",
        parameters: Type.Object({
          uri: Type.Optional(Type.String({ description: "Exact memory URI to delete" })),
          query: Type.Optional(Type.String({ description: "Search query to find memory URI" })),
          targetUri: Type.Optional(
            Type.String({ description: "Search scope URI (default: plugin config)" }),
          ),
          limit: Type.Optional(Type.Number({ description: "Search limit (default: 5)" })),
          scoreThreshold: Type.Optional(
            Type.Number({ description: "Minimum score (0-1, default: plugin config)" }),
          ),
        }),
        async execute(_toolCallId: string, params: Record<string, unknown>) {
          if (isBypassedSession(ctx)) {
            return makeBypassedToolResult("memory_forget");
          }
          const session = resolvePluginSessionRouting(ctx);
          const peerId = resolveToolSearchPeerId(ctx, session);
          const client = await getClient();
          const uri = (params as { uri?: string }).uri;
          if (uri) {
            if (!isMemoryUri(uri)) {
              return textResult(`Refusing to delete non-memory URI: ${uri}`, { action: "rejected", uri });
            }
            await client.deleteUri(uri, peerId);
            return textResult(`Forgotten: ${uri}`, { action: "deleted", uri });
          }

          const query = (params as { query?: string }).query;
          if (!query) {
            return textResult("Provide uri or query.", { error: "missing_param" });
          }

          const limit =
            typeof (params as { limit?: number }).limit === "number"
              ? Math.max(1, Math.floor((params as { limit: number }).limit))
              : 5;
          const scoreThreshold =
            typeof (params as { scoreThreshold?: number }).scoreThreshold === "number"
              ? Math.max(0, Math.min(1, (params as { scoreThreshold: number }).scoreThreshold))
              : cfg.recallScoreThreshold;
          const targetUri =
            typeof (params as { targetUri?: string }).targetUri === "string"
              ? (params as { targetUri: string }).targetUri
              : cfg.targetUri;
          const requestLimit = Math.max(limit * 4, 20);

          const result = await client.find(
            query,
            {
              targetUri,
              limit: requestLimit,
              scoreThreshold: 0,
              actorPeerId: peerId,
            },
          );
          const candidates = postProcessMemories(result.memories ?? [], {
            limit: requestLimit,
            scoreThreshold,
            leafOnly: true,
          }).filter((item) => isMemoryUri(item.uri));
          if (candidates.length === 0) {
            return textResult("No matching leaf memory candidates found. Try a more specific query.", { action: "none", scoreThreshold });
          }
          const top = candidates[0];
          if (candidates.length === 1 && clampScore(top.score) >= 0.85) {
            await client.deleteUri(top.uri, peerId);
            return textResult(`Forgotten: ${top.uri}`, { action: "deleted", uri: top.uri, score: top.score ?? 0 });
          }

          const list = candidates
            .map((item) => `- ${item.uri} (${(clampScore(item.score) * 100).toFixed(0)}%)`)
            .join("\n");

          return textResult(`Found ${candidates.length} candidates. Specify uri:\n${list}`, { action: "candidates", candidates, scoreThreshold, requestLimit });
        },
      }),
      { name: "memory_forget" },
    );

    registerOpenVikingTool(
      (ctx: ToolContext) => ({
        name: "ov_archive_search",
        label: "Archive Search (OpenViking)",
        description:
          "Keyword-grep across all archived original conversation messages of the current session. " +
          "Use this whenever the [Session History Summary] does not contain the specific detail " +
          "the user is asking about. Extract 2-3 concrete entity words from the question " +
          "(names, places, objects, dates) and search each separately. " +
          "Only conclude information is unavailable after trying at least 2 different keyword variations.",
        parameters: Type.Object({
          query: Type.String({
            description:
              "A single keyword or short phrase to grep. Use concrete nouns, names, dates, " +
              "or distinctive phrases. Case-insensitive. Prefer entity words over full sentences.",
          }),
          archiveId: Type.Optional(
            Type.String({
              description: 'Optional: limit search to one archive (e.g. "archive_005")',
            }),
          ),
        }),
        async execute(_toolCallId: string, params: Record<string, unknown>) {
          if (isBypassedSession(ctx)) {
            return makeBypassedToolResult("ov_archive_search");
          }
          rememberSessionAgentId(ctx);
          const sessionId = ctx.sessionId ?? "";
          const sessionKey = ctx.sessionKey ?? "";
          if (!sessionId && !sessionKey) {
            return textResult("Error: no active session.", { error: "no_session" });
          }
          const ovSessionId = openClawSessionToOvStorageId(ctx.sessionId, ctx.sessionKey);
          const query = String((params as { query?: string }).query ?? "").trim();
          const archiveId = (params as { archiveId?: string }).archiveId;

          if (!query) {
            return textResult("Error: query is required.", { error: "missing_param", param: "query" });
          }

          const escapedQuery = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
          api.logger.info?.(`openviking: ov_archive_search query="${query}" escaped="${escapedQuery}" archive=${archiveId ?? "all"} session=${ovSessionId}`);

          try {
            const client = await getClient();
            const started = Date.now();
            const result = await client.grepSessionArchives(ovSessionId, escapedQuery, {
              archiveId,
              caseInsensitive: true,
            });
            const traceResults: RecallTraceResult[] = (result.matches ?? [])
              .slice(0, cfg.traceRecallMaxResultsPerSearch)
              .map((match) => ({
                uri: match.uri,
                resourceType: "archive",
                abstractPreview: previewText(match.content, cfg.traceRecallPreviewChars),
                resultType: "archive_match",
              }));
            const recordArchiveTrace = async (displayed: Array<{ uri: string; line: number; content: string }>) => {
              await traceRecorder?.recordAndFlush({
                schemaVersion: "1.0",
                traceId: createTraceId("ov_archive_search"),
                ts: Date.now(),
                sessionId: ctx.sessionId,
                sessionKey: ctx.sessionKey,
                ovSessionId,
                agentId: ctx.agentId,
                source: "ov_archive_search",
                operationType: "archive_grep",
                resourceTypes: ["session"],
                trigger: {
                  ...boundTraceQuery(query, cfg.traceRecallQueryMaxChars),
                  derivedKeywords: [query],
                },
                searches: [{
                  resourceType: "archive",
                  targetUriResolved: archiveId
                    ? `${userSessionUri(ovSessionId)}/history/${archiveId}`
                    : `${userSessionUri(ovSessionId)}/history`,
                  limit: cfg.traceRecallMaxResultsPerSearch,
                  durationMs: Date.now() - started,
                  total: result.matches?.length ?? result.count ?? 0,
                  results: traceResults,
                  archiveId,
                  caseInsensitive: true,
                }],
                selected: displayed.map((match) => ({
                  uri: match.uri,
                  resourceType: "archive",
                  line: match.line,
                  abstractPreview: previewText(match.content, cfg.traceRecallPreviewChars),
                  displayed: true,
                })),
                stats: {
                  candidateCount: result.matches?.length ?? result.count ?? 0,
                  selectedCount: displayed.length,
                  injectedCount: 0,
                },
              });
            };

            if (!result.matches || result.matches.length === 0) {
              await recordArchiveTrace([]);
              return textResult(
                `No matches found for "${query}". Try a different keyword — ` +
                  "the original conversation may use different wording than the question. " +
                  "Try synonyms, related terms, or shorter fragments.",
                { query, matchCount: 0 },
              );
            }

            const MAX_MATCHES = 12;
            const MAX_LINE_LEN = 1500;
            const shown = result.matches.slice(0, MAX_MATCHES);
            await recordArchiveTrace(shown);
            const blocks = shown.map((m, i) => {
              const archiveTag = m.uri.match(/archive_\d+/)?.[0] ?? "unknown";
              const truncated = m.content.length > MAX_LINE_LEN
                ? m.content.slice(0, MAX_LINE_LEN) + "…(truncated)"
                : m.content;
              return `## Match ${i + 1}: ${archiveTag} (line ${m.line})\n${truncated}`;
            });

            const header = `Found ${result.matches.length} match(es) for "${query}"` +
              (result.matches.length > MAX_MATCHES ? ` (showing first ${MAX_MATCHES})` : "") + ":";

            return textResult(header + "\n\n" + blocks.join("\n\n"), { query, matchCount: result.matches.length });
          } catch (err: unknown) {
            const msg = err instanceof Error ? err.message : String(err);
            api.logger.error?.(`openviking: ov_archive_search error: ${msg}`);
            return textResult(`Archive search failed: ${msg}`, { error: msg });
          }
        },
      }),
      { name: "ov_archive_search" },
    );

    registerOpenVikingTool((ctx: ToolContext) => ({
      name: "ov_archive_expand",
      label: "Archive Expand (OpenViking)",
      description:
        "Retrieve original messages from a compressed session archive. " +
        "Use when a session summary lacks specific details " +
        "such as exact commands, file paths, code snippets, or config values. " +
        "Check [Archive Index] to find the right archive ID.",
      parameters: Type.Object({
        archiveId: Type.String({
          description:
            'Archive ID from [Archive Index] (e.g. "archive_002")',
        }),
      }),
      async execute(_toolCallId: string, params: Record<string, unknown>) {
        if (isBypassedSession(ctx)) {
          return makeBypassedToolResult("ov_archive_expand");
        }
        const session = resolvePluginSessionRouting(ctx);
        const archiveId = String((params as { archiveId?: string }).archiveId ?? "").trim();
        const sessionId = session.sessionId ?? "";
        api.logger.info?.(`openviking: ov_archive_expand invoked (archiveId=${archiveId || "(empty)"}, sessionId=${sessionId || "(empty)"})`);

        if (!archiveId) {
          api.logger.warn?.(`openviking: ov_archive_expand missing archiveId`);
          return textResult("Error: archiveId is required.", { error: "missing_param", param: "archiveId" });
        }

        if (!session.ovSessionId) {
          return textResult("Error: no active session.", { error: "no_session" });
        }

        try {
          const client = await getClient();
          const detail = await client.getSessionArchive(
            session.ovSessionId,
            archiveId,
          );

          const header = [
            `## ${detail.archive_id}`,
            detail.abstract ? `**Summary**: ${detail.abstract}` : "",
            `**Messages**: ${detail.messages.length}`,
            "",
          ].filter(Boolean).join("\n");

          const body = detail.messages
            .map((m: OVMessage) => formatMessageFaithful(m))
            .join("\n\n");

          api.logger.info?.(`openviking: ov_archive_expand expanded ${detail.archive_id}, messages=${detail.messages.length}, chars=${body.length}, sessionId=${sessionId}`);
          return textResult(`${header}\n${body}`, {
              action: "expanded",
              archiveId: detail.archive_id,
              messageCount: detail.messages.length,
              sessionId,
              ovSessionId: session.ovSessionId,
          });
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          api.logger.warn?.(`openviking: ov_archive_expand failed (archiveId=${archiveId}, sessionId=${sessionId}): ${msg}`);
          return textResult(`Failed to expand ${archiveId}: ${msg}`, { error: msg, archiveId, sessionId, ovSessionId: session.ovSessionId });
        }
      },
    }), { name: "ov_archive_expand" });

    registerOpenVikingTool(
      (ctx: ToolContext) => ({
        name: "openviking_tool_result_read",
        label: "Tool Result Read (OpenViking)",
        description:
          "Restore the full original content of a tool result that was externalized by OpenViking. " +
          "Use when a previous tool result was externalized and only a preview is visible — " +
          "the preview contains a [tool-result-ref] or viking://user/sessions/.../tool-results/... URI. " +
          "\"Read\" tool returns the same truncated preview; this tool returns the complete content. " +
          "To read all content: pass offset=0 and a limit large enough to cover the whole result " +
          "(e.g. limit=100000). Use offset/limit for paging only when you need a specific section.",
        parameters: Type.Object({
          tool_output_ref: Type.String({
            description:
              "Exact OV URI from the preview, e.g. viking://user/sessions/<session_id>/tool-results/<tool_result_id>",
          }),
          offset: Type.Optional(Type.Number({ description: "Unicode character offset. Default: 0" })),
          limit: Type.Optional(Type.Number({ description: "Maximum Unicode characters to read. Default: 20000" })),
        }),
        async execute(_toolCallId: string, params: Record<string, unknown>) {
          if (isBypassedSession(ctx)) {
            return makeBypassedToolResult("openviking_tool_result_read");
          }
          const session = resolvePluginSessionRouting(ctx);
          if (!session.ovSessionId) {
            return textResult("Error: no active session.", { error: "no_session" });
          }

          const parsed = parseToolResultRef(params.tool_output_ref ?? params.ref ?? params.uri);
          if (!parsed) {
            return textResult("Error: tool_output_ref must be a viking://user/sessions/.../tool-results/... URI.", { error: "invalid_tool_output_ref" });
          }
          if (parsed.sessionId !== session.ovSessionId) {
            return textResult("Error: refusing to read a tool result from another session.", {
                error: "session_mismatch",
                requestedSessionId: parsed.sessionId,
                currentSessionId: session.ovSessionId,
            });
          }

          const offset = Math.max(0, getOptionalInteger(params.offset, 0));
          const limit = getOptionalInteger(params.limit, 20_000);
          if (limit < -1) {
            return textResult("Error: limit must be -1 or greater than or equal to 0.", { error: "invalid_limit", limit });
          }

          try {
            const client = await getClient();
            const result = await client.readToolResult(
              session.ovSessionId,
              parsed.toolResultId,
              { offset, limit, includeMetadata: true },
            );
            const returnedChars = result.content.length;
            const nextOffset = result.offset + returnedChars;
            const text = result.content || "(empty tool result chunk)";
            return textResult(text, {
                action: "read",
                tool_output_ref: parsed.ref,
                tool_result_id: result.tool_result_id,
                offset: result.offset,
                limit: result.limit,
                returned_chars: returnedChars,
                total_chars: result.total_chars,
                has_more: result.has_more,
                next_offset: result.has_more ? nextOffset : null,
                metadata: result.metadata ?? null,
            });
          } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            api.logger.warn?.(`openviking: openviking_tool_result_read failed: ${msg}`);
            return textResult(`Failed to read tool result: ${msg}`, { error: msg, tool_output_ref: parsed.ref });
          }
        },
      }),
      { name: "openviking_tool_result_read" },
    );

    registerOpenVikingTool(
      (ctx: ToolContext) => ({
        name: "openviking_tool_result_search",
        label: "Tool Result Search (OpenViking)",
        description:
          "Search inside an externalized tool result for a keyword. " +
          "Use when you need to find specific content in a large externalized result, " +
          "before reading it with openviking_tool_result_read. " +
          "Returns matching snippets with their character offsets.",
        parameters: Type.Object({
          tool_output_ref: Type.String({
            description:
              "Exact OV URI from the preview, e.g. viking://user/sessions/<session_id>/tool-results/<tool_result_id>",
          }),
          query: Type.String({ description: "Keyword or exact text to search for" }),
          limit: Type.Optional(Type.Number({ description: "Maximum matches. Default: 20" })),
          context_chars: Type.Optional(Type.Number({ description: "Characters around each match. Default: 300" })),
        }),
        async execute(_toolCallId: string, params: Record<string, unknown>) {
          if (isBypassedSession(ctx)) {
            return makeBypassedToolResult("openviking_tool_result_search");
          }
          const session = resolvePluginSessionRouting(ctx);
          if (!session.ovSessionId) {
            return textResult("Error: no active session.", { error: "no_session" });
          }

          const parsed = parseToolResultRef(params.tool_output_ref ?? params.ref ?? params.uri);
          if (!parsed) {
            return textResult("Error: tool_output_ref must be a viking://user/sessions/.../tool-results/... URI.", { error: "invalid_tool_output_ref" });
          }
          if (parsed.sessionId !== session.ovSessionId) {
            return textResult("Error: refusing to search a tool result from another session.", {
                error: "session_mismatch",
                requestedSessionId: parsed.sessionId,
                currentSessionId: session.ovSessionId,
            });
          }

          const query = String(params.query ?? "").trim();
          if (!query) {
            return textResult("Error: query is required.", { error: "missing_param", param: "query" });
          }
          const limit = getPositiveInteger(params.limit, 20);
          const contextChars = Math.max(
            0,
            getOptionalInteger(params.context_chars ?? params.contextChars, 300),
          );

          try {
            const client = await getClient();
            const result = await client.searchToolResult(
              session.ovSessionId,
              parsed.toolResultId,
              query,
              { limit, contextChars },
            );
            const matches = result.matches ?? [];
            const text = matches.length
              ? [
                  `Found ${matches.length} match(es) for "${query}" in ${parsed.ref}:`,
                  "",
                  ...matches.map((match, index) =>
                    `## Match ${index + 1} (offset ${match.offset})\n${match.snippet}`,
                  ),
                ].join("\n")
              : `No matches found for "${query}" in ${parsed.ref}.`;
            return textResult(text, {
                action: "searched",
                tool_output_ref: parsed.ref,
                tool_result_id: result.tool_result_id,
                query,
                match_count: matches.length,
                matches,
            });
          } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            api.logger.warn?.(`openviking: openviking_tool_result_search failed: ${msg}`);
            return textResult(`Failed to search tool result: ${msg}`, { error: msg, tool_output_ref: parsed.ref, query });
          }
        },
      }),
      { name: "openviking_tool_result_search" },
    );

    registerOpenVikingTool(
      (ctx: ToolContext) => ({
        name: "openviking_tool_result_list",
        label: "Tool Result List (OpenViking)",
        description:
          "List externalized tool results for the current session. " +
          "Use to discover available refs before calling openviking_tool_result_read. " +
          "Optionally filter by tool_name to narrow down results.",
        parameters: Type.Object({
          tool_name: Type.Optional(Type.String({ description: "Optional exact tool name filter" })),
          limit: Type.Optional(Type.Number({ description: "Maximum results. Default: 50" })),
        }),
        async execute(_toolCallId: string, params: Record<string, unknown>) {
          if (isBypassedSession(ctx)) {
            return makeBypassedToolResult("openviking_tool_result_list");
          }
          const session = resolvePluginSessionRouting(ctx);
          if (!session.ovSessionId) {
            return textResult("Error: no active session.", { error: "no_session" });
          }

          const toolName =
            typeof params.tool_name === "string" && params.tool_name.trim()
              ? params.tool_name.trim()
              : typeof params.toolName === "string" && params.toolName.trim()
                ? params.toolName.trim()
                : undefined;
          const limit = getPositiveInteger(params.limit, 50);

          try {
            const client = await getClient();
            const result = await client.listToolResults(
              session.ovSessionId,
              { toolName, limit },
            );
            const items = result.tool_results ?? [];
            const text = items.length
              ? [
                  `Found ${items.length} externalized tool result(s) in current session:`,
                  "",
                  ...items.map((item, index) => {
                    const ref = typeof item.storage_uri === "string" ? item.storage_uri : "(missing ref)";
                    const name = typeof item.tool_name === "string" ? item.tool_name : "tool";
                    const chars = typeof item.original_chars === "number" ? item.original_chars : "unknown";
                    const created = typeof item.created_at === "string" ? ` created_at=${item.created_at}` : "";
                    return `${index + 1}. ${name} original_chars=${chars}${created}\nref: ${ref}`;
                  }),
                ].join("\n")
              : toolName
                ? `No externalized tool results found for tool "${toolName}" in current session.`
                : "No externalized tool results found in current session.";
            return textResult(text, {
                action: "listed",
                session_id: session.ovSessionId,
                tool_name: toolName ?? null,
                count: items.length,
                tool_results: items,
            });
          } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            api.logger.warn?.(`openviking: openviking_tool_result_list failed: ${msg}`);
            return textResult(`Failed to list tool results: ${msg}`, { error: msg, session_id: session.ovSessionId, tool_name: toolName ?? null });
          }
        },
      }),
      { name: "openviking_tool_result_list" },
    );

    let contextEngineRef: ContextEngineWithCommit | null = null;
    const sessionAgentResolver = createSessionAgentResolver(cfg.peer_prefix);
    const rememberSessionAgentId = (ctx: SessionAgentLookup) => {
      sessionAgentResolver.remember(ctx);
    };
    const resolveAgentId = (
      sessionId?: string,
      sessionKey?: string,
      ovSessionId?: string,
    ): string => {
      const sid = typeof sessionId === "string" ? sessionId.trim() : "";
      const sk = typeof sessionKey === "string" ? sessionKey.trim() : "";
      const ovSid = typeof ovSessionId === "string" ? ovSessionId.trim() : "";
      const result = sessionAgentResolver.resolve(sid, sk, ovSid);
      if (cfg.logFindRequests) {
        api.logger.info(
          `openviking: resolveAgentId ${JSON.stringify({
            sessionId: sid || "(empty)",
            sessionKey: sk || "(empty)",
            ovSessionId: ovSid || "(empty)",
            parsedConfigPeerPrefix: cfg.peer_prefix,
            peerRole: cfg.peer_role,
            mappedResolvedAgentId: result.mappedResolvedAgentId,
            resolvedBeforeSanitize: result.resolvedBeforeSanitize,
            resolved: result.resolved,
            branch: result.branch,
            aliases: result.aliases,
            fromExplicitBinding: result.fromExplicitBinding,
          })}`,
        );
      }
      return result.resolved;
    };

    api.on("session_start", async (_event: unknown, ctx?: HookAgentContext) => {
      rememberSessionAgentId(ctx ?? {});
    });
    api.on("session_end", async (_event: unknown, ctx?: HookAgentContext) => {
      rememberSessionAgentId(ctx ?? {});
    });
    api.on("before_reset", async (_event: unknown, ctx?: HookAgentContext) => {
      if (isBypassedSession(ctx)) {
        verboseRoutingInfo(
          `openviking: bypassing before_reset due to session pattern match (sessionKey=${ctx?.sessionKey ?? "none"}, sessionId=${ctx?.sessionId ?? "none"})`,
        );
        return;
      }
      const sessionId = ctx?.sessionId;
      if (sessionId && contextEngineRef) {
        try {
          const ok = await contextEngineRef.commitOVSession({
            sessionId,
            sessionKey: ctx?.sessionKey,
          });
          if (ok) {
            api.logger.info(`openviking: committed OV session on reset for session=${sessionId}`);
          }
        } catch (err) {
          api.logger.warn(`openviking: failed to commit OV session on reset: ${String(err)}`);
        }
      }
    });
    api.on("after_compaction", async (_event: unknown, _ctx?: HookAgentContext) => {
      // Reserved hook registration for future post-compaction memory integration.
    });

    if (typeof api.registerContextEngine === "function") {
      api.registerContextEngine(contextEnginePlugin.id, () => {
        contextEngineRef = createMemoryOpenVikingContextEngine({
          id: contextEnginePlugin.id,
          name: contextEnginePlugin.name,
          version: "0.1.0",
          cfg,
          logger: api.logger,
          getClient,
          resolveAgentId,
          rememberSessionAgentId,
          traceRecorder,
        });
        return contextEngineRef;
      });
      api.logger.info(
        "openviking: registered context-engine (assemble=archive+active+auto-recall, afterTurn=auto-capture, session→OV id=uuid-or-sha256 + diag/Phase2 options)",
      );
    } else {
      api.logger.warn(
        "openviking: registerContextEngine is unavailable; context-engine behavior will not run",
      );
    }

    registerSetupCli(api);

    api.registerService({
      id: "openviking",
      start: async (ctx?: unknown) => {
        const routeRegistered = registerRecallTraceRoutes(ctx);
        await (await getClient()).healthCheck().catch(() => {});
        api.logger.info(
          `openviking: initialized (url: ${cfg.baseUrl}, targetUri: ${cfg.targetUri}, search: hybrid endpoint)`,
        );
        if (routeRegistered) {
          api.logger.info("openviking: registered recall trace Gateway routes");
        } else {
          api.logger.warn?.("openviking: recall trace Gateway route adapter unavailable; use ov_recall_trace tool or /ov-recall-trace command");
        }
      },
      stop: () => {
        api.logger.info("openviking: stopped");
      },
    });
  },
};

export default contextEnginePlugin;
