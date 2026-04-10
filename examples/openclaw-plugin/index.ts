import { spawn } from "node:child_process";
import { tmpdir } from "node:os";

import { Type } from "@sinclair/typebox";
import { memoryOpenVikingConfigSchema } from "./config.js";

import { OpenVikingClient, localClientCache, localClientPendingPromises, isMemoryUri } from "./client.js";
import type { FindResultItem, PendingClientEntry, CommitSessionResult, OVMessage } from "./client.js";
import { formatMessageFaithful } from "./context-engine.js";
import {
  compileSessionPatterns,
  isTranscriptLikeIngest,
  extractLatestUserText,
  sanitizeUserTextForCapture,
  shouldBypassSession,
} from "./text-utils.js";
import {
  clampScore,
  postProcessMemories,
  formatMemoryLines,
  toJsonLog,
  summarizeInjectionMemories,
  pickMemoriesForInjection,
} from "./memory-ranking.js";
import {
  IS_WIN,
  waitForHealthOrExit,
  quickHealthCheck,
  quickRecallPrecheck,
  withTimeout,
  resolvePythonCommand,
  prepareLocalPort,
} from "./process-manager.js";
import {
  createMemoryOpenVikingContextEngine,
  openClawSessionToOvStorageId,
} from "./context-engine.js";
import type { ContextEngineWithCommit } from "./context-engine.js";

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

type SessionAgentLookup = {
  agentId?: string;
  sessionId?: string;
  sessionKey?: string;
  ovSessionId?: string;
};

type SessionAgentResolveBranch =
  | "session_resolved"
  | "config_only_fallback"
  | "default_no_session";

export type SessionAgentResolveResult = {
  resolved: string;
  resolvedBeforeSanitize: string;
  branch: SessionAgentResolveBranch;
  mappedResolvedAgentId: string | null;
  aliases: string[];
  fromExplicitBinding: boolean;
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
};

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
  registerService: (service: {
    id: string;
    start: (ctx?: unknown) => void | Promise<void>;
    stop?: (ctx?: unknown) => void | Promise<void>;
  }) => void;
  registerContextEngine?: (id: string, factory: () => unknown) => void;
  on: (
    hookName: string,
    handler: (event: unknown, ctx?: HookAgentContext) => unknown,
    opts?: { priority?: number },
  ) => void;
};

const MAX_OPENVIKING_STDERR_LINES = 200;
const MAX_OPENVIKING_STDERR_CHARS = 256_000;
const AUTO_RECALL_TIMEOUT_MS = 5_000;
const RECALL_QUERY_MAX_CHARS = 4_000;

/**
 * OpenViking `UserIdentifier` allows only [a-zA-Z0-9_-] for agent_id
 * (see openviking_cli/session/user_id.py). OpenClaw ids may contain ":"
 * (e.g. session keys); never send raw colons in X-OpenViking-Agent.
 */
export function sanitizeOpenVikingAgentIdHeader(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) {
    return "default";
  }
  const normalized = trimmed
    .replace(/[^a-zA-Z0-9_-]/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_|_$/g, "");
  return normalized.length > 0 ? normalized : "ov_agent";
}

export type PreparedRecallQuery = {
  query: string;
  truncated: boolean;
  originalChars: number;
  finalChars: number;
};

export function prepareRecallQuery(rawText: string): PreparedRecallQuery {
  const sanitized = sanitizeUserTextForCapture(rawText).trim();
  const originalChars = sanitized.length;

  if (!sanitized) {
    return {
      query: "",
      truncated: false,
      originalChars: 0,
      finalChars: 0,
    };
  }

  const query =
    sanitized.length > RECALL_QUERY_MAX_CHARS
      ? sanitized.slice(0, RECALL_QUERY_MAX_CHARS).trim()
      : sanitized;

  return {
    query,
    truncated: sanitized.length > RECALL_QUERY_MAX_CHARS,
    originalChars,
    finalChars: query.length,
  };
}

function extractAgentIdFromSessionKey(sessionKey?: string): string | undefined {
  const raw = typeof sessionKey === "string" ? sessionKey.trim() : "";
  if (!raw) {
    return undefined;
  }

  const match = raw.match(/^agent:([^:]+):/);
  const agentId = match?.[1]?.trim();
  return agentId || undefined;
}

function collectSessionAgentAliases(
  sessionId?: string,
  sessionKey?: string,
  ovSessionId?: string,
): string[] {
  const aliases = new Set<string>();
  const sid = typeof sessionId === "string" ? sessionId.trim() : "";
  const sk = typeof sessionKey === "string" ? sessionKey.trim() : "";
  const ovSid = typeof ovSessionId === "string" ? ovSessionId.trim() : "";

  if (sid) {
    aliases.add(sid);
  }
  if (sk) {
    aliases.add(sk);
  }
  if (ovSid) {
    aliases.add(ovSid);
  }

  if (!ovSid && (sid || sk)) {
    try {
      aliases.add(
        openClawSessionToOvStorageId(
          sid || undefined,
          sk || undefined,
        ),
      );
    } catch {
      /* need a resolvable OpenClaw session identity */
    }
  }

  return [...aliases];
}

export function createSessionAgentResolver(configAgentId: string) {
  const sessionAgentIds = new Map<string, string>();

  const remember = (ctx: SessionAgentLookup): void => {
    const sessionScopedAgentId =
      extractAgentIdFromSessionKey(ctx.sessionKey) ||
      extractAgentIdFromSessionKey(ctx.sessionId);
    const rawAgentId =
      (typeof ctx.agentId === "string" ? ctx.agentId.trim() : "") ||
      sessionScopedAgentId ||
      "";
    if (!rawAgentId) {
      return;
    }

    const resolvedBeforeSanitize =
      !configAgentId || configAgentId === "default"
        ? rawAgentId
        : `${configAgentId}_${rawAgentId}`;
    const resolved = sanitizeOpenVikingAgentIdHeader(resolvedBeforeSanitize);
    for (const alias of collectSessionAgentAliases(ctx.sessionId, ctx.sessionKey, ctx.ovSessionId)) {
      sessionAgentIds.set(alias, resolved);
    }
  };

  const resolve = (
    sessionId?: string,
    sessionKey?: string,
    ovSessionId?: string,
  ): SessionAgentResolveResult => {
    const aliases = collectSessionAgentAliases(sessionId, sessionKey, ovSessionId);
    const mappedAlias = aliases.find((alias) => sessionAgentIds.has(alias));
    const mappedResolvedAgentId = mappedAlias ? sessionAgentIds.get(mappedAlias) : undefined;
    const sessionScopedAgentId =
      extractAgentIdFromSessionKey(sessionKey) ||
      extractAgentIdFromSessionKey(sessionId);

    let resolvedBeforeSanitize: string;
    let resolved: string;
    let branch: SessionAgentResolveBranch;

    if (mappedResolvedAgentId) {
      resolvedBeforeSanitize = mappedResolvedAgentId;
      resolved = mappedResolvedAgentId;
      branch = "session_resolved";
    } else if (sessionScopedAgentId) {
      resolvedBeforeSanitize =
        !configAgentId || configAgentId === "default"
          ? sessionScopedAgentId
          : `${configAgentId}_${sessionScopedAgentId}`;
      resolved = sanitizeOpenVikingAgentIdHeader(resolvedBeforeSanitize);
      branch = "session_resolved";
    } else if (!configAgentId || configAgentId === "default") {
      resolvedBeforeSanitize = "default";
      resolved = "default";
      branch = "default_no_session";
    } else {
      resolvedBeforeSanitize = configAgentId;
      resolved = sanitizeOpenVikingAgentIdHeader(configAgentId);
      branch = "config_only_fallback";
    }

    // Only explicit agent observations are persisted via remember().
    // Fallback values must stay ephemeral so a later real ctx.agentId
    // can safely take over without inheriting a stale default binding.
    return {
      resolved,
      resolvedBeforeSanitize,
      branch,
      mappedResolvedAgentId: mappedResolvedAgentId ?? null,
      aliases,
      fromExplicitBinding: !!(mappedResolvedAgentId || sessionScopedAgentId),
    };
  };

  return {
    remember,
    resolve,
  };
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
    const cfg = memoryOpenVikingConfigSchema.parse(api.pluginConfig);
    const bypassSessionPatterns = compileSessionPatterns(cfg.bypassSessionPatterns);
    const rawAgentId = rawCfg.agentId;
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
      `openviking: loaded plugin config agentId="${cfg.agentId}" ` +
        `(raw plugins.entries.openviking.config.agentId=${JSON.stringify(rawAgentId ?? "(missing)")}; ` +
        `${
          cfg.agentId !== "default"
            ? "non-default → X-OpenViking-Agent is <configAgentId>_<ctx.agentId> (sanitized to [a-zA-Z0-9_-]) when hooks expose session agent; config-only if ctx.agentId unknown"
            : 'default → X-OpenViking-Agent follows OpenClaw ctx.agentId per session (e.g. "main")'
        })`,
    );
    const routingDebugLog = cfg.logFindRequests
      ? (msg: string) => {
          api.logger.info(msg);
        }
      : undefined;
    const tenantAccount = "";
    const tenantUser = "";
    const localCacheKey = `${cfg.mode}:${cfg.baseUrl}:${cfg.configPath}:${cfg.apiKey}:${tenantAccount}:${tenantUser}:${cfg.agentId}:${cfg.logFindRequests ? "1" : "0"}`;

    let clientPromise: Promise<OpenVikingClient>;
    let localProcess: ReturnType<typeof spawn> | null = null;
    let resolveLocalClient: ((c: OpenVikingClient) => void) | null = null;
    let rejectLocalClient: ((err: unknown) => void) | null = null;
    let localUnavailableReason: string | null = null;
    const markLocalUnavailable = (reason: string, err?: unknown) => {
      if (!localUnavailableReason) {
        localUnavailableReason = reason;
        api.logger.warn(
          `openviking: local mode marked unavailable (${reason})${err ? `: ${String(err)}` : ""}`,
        );
      }
      if (rejectLocalClient) {
        rejectLocalClient(
          err instanceof Error ? err : new Error(`openviking unavailable: ${reason}`),
        );
        rejectLocalClient = null;
      }
      resolveLocalClient = null;
    };

    if (cfg.mode === "local") {
      const cached = localClientCache.get(localCacheKey);
      if (cached) {
        localProcess = cached.process;
        clientPromise = Promise.resolve(cached.client);
      } else {
        const existingPending = localClientPendingPromises.get(localCacheKey);
        if (existingPending) {
          clientPromise = existingPending.promise;
        } else {
          const entry = {} as PendingClientEntry;
          entry.promise = new Promise<OpenVikingClient>((resolve, reject) => {
            entry.resolve = resolve;
            entry.reject = reject;
          });
          // Service startup can reject this shared promise before any hook/tool
          // awaits it. Attach a sink now so expected local-startup failures do
          // not surface as process-level unhandled rejections.
          void entry.promise.catch(() => {});
          clientPromise = entry.promise;
          localClientPendingPromises.set(localCacheKey, entry);
        }
      }
    } else {
      clientPromise = Promise.resolve(
        new OpenVikingClient(
          cfg.baseUrl,
          cfg.apiKey,
          cfg.agentId,
          cfg.timeoutMs,
          tenantAccount,
          tenantUser,
          routingDebugLog,
        ),
      );
    }

    const getClient = (): Promise<OpenVikingClient> => clientPromise;

    const isBypassedSession = (ctx?: {
      sessionId?: string;
      sessionKey?: string;
    }): boolean => shouldBypassSession(ctx ?? {}, bypassSessionPatterns);

    const makeBypassedToolResult = (toolName: string) => ({
      content: [
        {
          type: "text" as const,
          text: `OpenViking is bypassed for this session by bypassSessionPatterns; ${toolName} was skipped.`,
        },
      ],
      details: {
        action: "bypassed",
        reason: "session_bypassed",
        toolName,
      },
    });

    api.registerTool(
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
        }),
        async execute(_toolCallId: string, params: Record<string, unknown>) {
          if (isBypassedSession(ctx)) {
            return makeBypassedToolResult("memory_recall");
          }
          rememberSessionAgentId(ctx);
          const agentId = resolveAgentId(ctx.sessionId, ctx.sessionKey);
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
          const requestLimit = Math.max(limit * 4, 20);

          const recallClient = await getClient();
          if (cfg.logFindRequests) {
            api.logger.info(
              `openviking: memory_recall X-OpenViking-Agent="${agentId}" ` +
                `(plugin defaultAgentId="${recallClient.getDefaultAgentId()}" is unused when session context is present)`,
            );
          }

          let result;
          if (targetUri) {
            // 如果指定了目标 URI，只检索该位置
            result = await recallClient.find(
              query,
              {
                targetUri,
                limit: requestLimit,
                scoreThreshold: 0,
              },
              agentId,
            );
          } else {
            // 默认同时检索 user 和 agent 两个位置的记忆
            const [userSettled, agentSettled] = await Promise.allSettled([
              recallClient.find(
                query,
                {
                  targetUri: "viking://user/memories",
                  limit: requestLimit,
                  scoreThreshold: 0,
                },
                agentId,
              ),
              recallClient.find(
                query,
                {
                  targetUri: "viking://agent/memories",
                  limit: requestLimit,
                  scoreThreshold: 0,
                },
                agentId,
              ),
            ]);
            const userResult = userSettled.status === "fulfilled" ? userSettled.value : { memories: [] };
            const agentResult = agentSettled.status === "fulfilled" ? agentSettled.value : { memories: [] };
            // 合并两个位置的结果，去重
            const allMemories = [...(userResult.memories ?? []), ...(agentResult.memories ?? [])];
            const uniqueMemories = allMemories.filter((memory, index, self) =>
              index === self.findIndex((m) => m.uri === memory.uri)
            );
            const leafOnly = uniqueMemories.filter((m) => m.level === 2);
            result = {
              memories: leafOnly,
              total: leafOnly.length,
            };
          }

          const memories = postProcessMemories(result.memories ?? [], {
            limit,
            scoreThreshold,
          });
          if (memories.length === 0) {
            return {
              content: [{ type: "text", text: "No relevant OpenViking memories found." }],
              details: { count: 0, total: result.total ?? 0, scoreThreshold },
            };
          }
          return {
            content: [
              {
                type: "text",
                text: `Found ${memories.length} memories:\n\n${formatMemoryLines(memories)}`,
              },
            ],
            details: {
              count: memories.length,
              memories,
              total: result.total ?? memories.length,
              scoreThreshold,
              requestLimit,
            },
          };
        },
      }),
      { name: "memory_recall" },
    );

    api.registerTool(
      (ctx: ToolContext) => ({
        name: "memory_store",
        label: "Memory Store (OpenViking)",
        description:
          "Store text in OpenViking memory pipeline by writing to a session and running memory extraction.",
        parameters: Type.Object({
          text: Type.String({ description: "Information to store as memory source text" }),
          role: Type.Optional(Type.String({ description: "Session role, default user" })),
          sessionId: Type.Optional(Type.String({ description: "Existing OpenViking session ID" })),
        }),
        async execute(_toolCallId: string, params: Record<string, unknown>) {
          if (isBypassedSession(ctx)) {
            return makeBypassedToolResult("memory_store");
          }
          rememberSessionAgentId(ctx);
          const storeAgentId = resolveAgentId(ctx.sessionId, ctx.sessionKey);
          const { text } = params as { text: string };
          const role =
            typeof (params as { role?: string }).role === "string"
              ? (params as { role: string }).role
              : "user";
          const sessionIdIn = (params as { sessionId?: string }).sessionId;

          if (cfg.logFindRequests) {
            api.logger.info?.(
              `openviking: memory_store invoked (textLength=${text?.length ?? 0}, sessionId=${sessionIdIn ?? "auto"})`,
            );
          }

          let sessionId = sessionIdIn;
          let usedTempSession = false;
          try {
            const c = await getClient();
            if (!sessionId) {
              sessionId = `memory-store-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
              usedTempSession = true;
            }
            sessionId = openClawSessionToOvStorageId(sessionId, ctx.sessionKey);
            await c.addSessionMessage(sessionId, role, text, storeAgentId);
            const commitResult = await c.commitSession(sessionId, { wait: true, agentId: storeAgentId });
            const memoriesCount = totalCommitMemories(commitResult);
            if (commitResult.status === "failed") {
              api.logger.warn(
                `openviking: memory_store commit failed (sessionId=${sessionId}): ${commitResult.error ?? "unknown"}`,
              );
              return {
                content: [{ type: "text", text: `Memory extraction failed for session ${sessionId}: ${commitResult.error ?? "unknown"}` }],
                details: {
                  action: "failed",
                  sessionId,
                  status: "failed",
                  error: commitResult.error,
                  usedTempSession,
                },
              };
            }
            if (commitResult.status === "timeout") {
              api.logger.warn(
                `openviking: memory_store commit timed out (sessionId=${sessionId}), task_id=${commitResult.task_id ?? "none"}. Memories may still be extracting in background.`,
              );
              return {
                content: [{ type: "text", text: `Memory extraction timed out for session ${sessionId}. It may still complete in the background (task_id=${commitResult.task_id ?? "none"}).` }],
                details: {
                  action: "timeout",
                  sessionId,
                  status: "timeout",
                  taskId: commitResult.task_id,
                  usedTempSession,
                },
              };
            }
            if (memoriesCount === 0) {
              api.logger.warn(
                `openviking: memory_store committed but 0 memories extracted (sessionId=${sessionId}). ` +
                  "Check OpenViking server logs for embedding/extract errors (e.g. 401 API key, or extraction pipeline).",
              );
            } else {
              api.logger.info?.(`openviking: memory_store committed, memories=${memoriesCount}`);
            }
            return {
              content: [
                {
                  type: "text",
                  text: `Stored in OpenViking session ${sessionId} and committed ${memoriesCount} memories.`,
                },
              ],
              details: {
                action: "stored",
                sessionId,
                memoriesCount,
                status: commitResult.status,
                archived: commitResult.archived ?? false,
                usedTempSession,
              },
            };
          } catch (err) {
            api.logger.warn(`openviking: memory_store failed: ${String(err)}`);
            throw err;
          }
        },
      }),
      { name: "memory_store" },
    );

    api.registerTool(
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
          rememberSessionAgentId(ctx);
          const agentId = resolveAgentId(ctx.sessionId, ctx.sessionKey);
          const client = await getClient();
          const uri = (params as { uri?: string }).uri;
          if (uri) {
            if (!isMemoryUri(uri)) {
              return {
                content: [{ type: "text", text: `Refusing to delete non-memory URI: ${uri}` }],
                details: { action: "rejected", uri },
              };
            }
            await client.deleteUri(uri, agentId);
            return {
              content: [{ type: "text", text: `Forgotten: ${uri}` }],
              details: { action: "deleted", uri },
            };
          }

          const query = (params as { query?: string }).query;
          if (!query) {
            return {
              content: [{ type: "text", text: "Provide uri or query." }],
              details: { error: "missing_param" },
            };
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
            },
            agentId,
          );
          const candidates = postProcessMemories(result.memories ?? [], {
            limit: requestLimit,
            scoreThreshold,
            leafOnly: true,
          }).filter((item) => isMemoryUri(item.uri));
          if (candidates.length === 0) {
            return {
              content: [
                {
                  type: "text",
                  text: "No matching leaf memory candidates found. Try a more specific query.",
                },
              ],
              details: { action: "none", scoreThreshold },
            };
          }
          const top = candidates[0];
          if (candidates.length === 1 && clampScore(top.score) >= 0.85) {
            await client.deleteUri(top.uri, agentId);
            return {
              content: [{ type: "text", text: `Forgotten: ${top.uri}` }],
              details: { action: "deleted", uri: top.uri, score: top.score ?? 0 },
            };
          }

          const list = candidates
            .map((item) => `- ${item.uri} (${(clampScore(item.score) * 100).toFixed(0)}%)`)
            .join("\n");

          return {
            content: [
              {
                type: "text",
                text: `Found ${candidates.length} candidates. Specify uri:\n${list}`,
              },
            ],
            details: { action: "candidates", candidates, scoreThreshold, requestLimit },
          };
        },
      }),
      { name: "memory_forget" },
    );
    api.registerTool((ctx: ToolContext) => ({
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
        rememberSessionAgentId(ctx);
        const archiveId = String((params as { archiveId?: string }).archiveId ?? "").trim();
        const sessionId = ctx.sessionId ?? "";
        api.logger.info?.(`openviking: ov_archive_expand invoked (archiveId=${archiveId || "(empty)"}, sessionId=${sessionId || "(empty)"})`);

        if (!archiveId) {
          api.logger.warn?.(`openviking: ov_archive_expand missing archiveId`);
          return {
            content: [{ type: "text", text: "Error: archiveId is required." }],
            details: { error: "missing_param", param: "archiveId" },
          };
        }

        const sessionKey = ctx.sessionKey ?? "";
        if (!sessionId && !sessionKey) {
          return {
            content: [{ type: "text", text: "Error: no active session." }],
            details: { error: "no_session" },
          };
        }
        const ovSessionId = openClawSessionToOvStorageId(
          ctx.sessionId,
          ctx.sessionKey,
        );

        try {
          const client = await getClient();
          const agentId = resolveAgentId(ctx.sessionId, ctx.sessionKey);
          const detail = await client.getSessionArchive(
            ovSessionId,
            archiveId,
            agentId,
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
          return {
            content: [{ type: "text", text: `${header}\n${body}` }],
            details: {
              action: "expanded",
              archiveId: detail.archive_id,
              messageCount: detail.messages.length,
              sessionId,
              ovSessionId,
            },
          };
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          api.logger.warn?.(`openviking: ov_archive_expand failed (archiveId=${archiveId}, sessionId=${sessionId}): ${msg}`);
          return {
            content: [{ type: "text", text: `Failed to expand ${archiveId}: ${msg}` }],
            details: { error: msg, archiveId, sessionId, ovSessionId },
          };
        }
      },
    }));

    let contextEngineRef: ContextEngineWithCommit | null = null;
    const sessionAgentResolver = createSessionAgentResolver(cfg.agentId);
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
            parsedConfigAgentId: cfg.agentId,
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
    api.on("before_prompt_build", async (event: unknown, ctx?: HookAgentContext) => {
      rememberSessionAgentId(ctx ?? {});

      if (cfg.logFindRequests) {
        api.logger.info(
          `openviking: hook before_prompt_build ctx=${JSON.stringify({
            sessionId: ctx?.sessionId,
            sessionKey: ctx?.sessionKey,
            agentId: ctx?.agentId,
          })}`,
        );
      }
      if (isBypassedSession(ctx)) {
        verboseRoutingInfo(
          `openviking: bypassing before_prompt_build due to session pattern match (sessionKey=${ctx?.sessionKey ?? "none"}, sessionId=${ctx?.sessionId ?? "none"})`,
        );
        return;
      }
      const agentId = resolveAgentId(ctx?.sessionId, ctx?.sessionKey);
      let client: OpenVikingClient;
      try {
        client = await withTimeout(
          getClient(),
          5000,
          "openviking: client initialization timeout (OpenViking service not ready yet)"
        );
      } catch (err) {
        api.logger.warn?.(`openviking: failed to get client: ${String(err)}`);
        return;
      }

      const eventObj = (event ?? {}) as { messages?: unknown[]; prompt?: string };
      const latestUserText = extractLatestUserText(eventObj.messages);
      const rawRecallQuery =
        latestUserText ||
        (typeof eventObj.prompt === "string" ? eventObj.prompt.trim() : "");
      const recallQuery = prepareRecallQuery(rawRecallQuery);
      const queryText = recallQuery.query;
      if (!queryText) {
        return;
      }
      if (recallQuery.truncated) {
        verboseRoutingInfo(
          `openviking: recall query truncated (` +
            `chars=${recallQuery.originalChars}->${recallQuery.finalChars})`,
        );
      }

      const prependContextParts: string[] = [];

      if (cfg.autoRecall && queryText.length >= 5) {
        const precheck = await quickRecallPrecheck(cfg.mode, cfg.baseUrl, cfg.port, localProcess);
        if (!precheck.ok) {
          verboseRoutingInfo(
            `openviking: skipping auto-recall because precheck failed (${precheck.reason})`,
          );
        } else {
          try {
            await withTimeout(
              (async () => {
                const candidateLimit = Math.max(cfg.recallLimit * 4, 20);
                const [userSettled, agentSettled] = await Promise.allSettled([
                  client.find(queryText, {
                    targetUri: "viking://user/memories",
                    limit: candidateLimit,
                    scoreThreshold: 0,
                  }, agentId),
                  client.find(queryText, {
                    targetUri: "viking://agent/memories",
                    limit: candidateLimit,
                    scoreThreshold: 0,
                  }, agentId),
                ]);

                const userResult = userSettled.status === "fulfilled" ? userSettled.value : { memories: [] };
                const agentResult = agentSettled.status === "fulfilled" ? agentSettled.value : { memories: [] };
                if (userSettled.status === "rejected") {
                  api.logger.warn(`openviking: user memories search failed: ${String(userSettled.reason)}`);
                }
                if (agentSettled.status === "rejected") {
                  api.logger.warn(`openviking: agent memories search failed: ${String(agentSettled.reason)}`);
                }

                const allMemories = [...(userResult.memories ?? []), ...(agentResult.memories ?? [])];
                const uniqueMemories = allMemories.filter((memory, index, self) =>
                  index === self.findIndex((m) => m.uri === memory.uri)
                );
                const leafOnly = uniqueMemories.filter((m) => m.level === 2);
                const processed = postProcessMemories(leafOnly, {
                  limit: candidateLimit,
                  scoreThreshold: cfg.recallScoreThreshold,
                });
                const memories = pickMemoriesForInjection(processed, cfg.recallLimit, queryText);

                if (memories.length > 0) {
                  const { lines: memoryLines, estimatedTokens } = await buildMemoryLinesWithBudget(
                    memories,
                    (uri) => client.read(uri, agentId),
                    {
                      recallPreferAbstract: cfg.recallPreferAbstract,
                      recallMaxContentChars: cfg.recallMaxContentChars,
                      recallTokenBudget: cfg.recallTokenBudget,
                    },
                  );
                  const memoryContext = memoryLines.join("\n");
                  verboseRoutingInfo(
                    `openviking: injecting ${memoryLines.length} memories (~${estimatedTokens} tokens, budget=${cfg.recallTokenBudget})`,
                  );
                  verboseRoutingInfo(
                    `openviking: inject-detail ${toJsonLog({ count: memories.length, memories: summarizeInjectionMemories(memories) })}`,
                  );
                  prependContextParts.push(
                    "<relevant-memories>\nThe following OpenViking memories may be relevant:\n" +
                      `${memoryContext}\n` +
                    "</relevant-memories>",
                  );
                }
              })(),
              AUTO_RECALL_TIMEOUT_MS,
              "openviking: auto-recall search timeout",
            );
          } catch (err) {
            api.logger.warn(`openviking: auto-recall failed: ${String(err)}`);
          }
        }
      }

      if (cfg.ingestReplyAssist) {
        const decision = isTranscriptLikeIngest(queryText, {
          minSpeakerTurns: cfg.ingestReplyAssistMinSpeakerTurns,
          minChars: cfg.ingestReplyAssistMinChars,
        });
        if (decision.shouldAssist) {
          verboseRoutingInfo(
            `openviking: ingest-reply-assist applied (reason=${decision.reason}, speakerTurns=${decision.speakerTurns}, chars=${decision.chars})`,
          );
          prependContextParts.push(
            "<ingest-reply-assist>\n" +
              "The latest user input looks like a multi-speaker transcript used for memory ingestion.\n" +
              "Reply with 1-2 concise sentences to acknowledge or summarize key points.\n" +
              "Do not output NO_REPLY or an empty reply.\n" +
              "Do not fabricate facts beyond the provided transcript and recalled memories.\n" +
              "</ingest-reply-assist>",
          );
        }
      }

      if (prependContextParts.length > 0) {
        return {
          prependContext: prependContextParts.join("\n\n"),
        };
      }
    });
    api.on("agent_end", async (_event: unknown, ctx?: HookAgentContext) => {
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
          const ok = await contextEngineRef.commitOVSession(sessionId, ctx?.sessionKey);
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
          quickPrecheck:
            cfg.mode === "local"
              ? () => quickRecallPrecheck(cfg.mode, cfg.baseUrl, cfg.port, localProcess)
              : undefined,
          resolveAgentId,
          rememberSessionAgentId,
        });
        return contextEngineRef;
      });
      api.logger.info(
        "openviking: registered context-engine (before_prompt_build=auto-recall, afterTurn=auto-capture, assemble=archive+active, session→OV id=uuid-or-sha256 + diag/Phase2 options)",
      );
    } else {
      api.logger.warn(
        "openviking: registerContextEngine is unavailable; context-engine behavior will not run",
      );
    }

    api.registerService({
      id: "openviking",
      start: async () => {
        // Claim the pending entry — only the first start() call to claim it spawns the process.
        // Subsequent start() calls (from other registrations sharing the same promise) fall through.
        const pendingEntry = localClientPendingPromises.get(localCacheKey);
        const isSpawner = cfg.mode === "local" && !!pendingEntry;
        if (isSpawner) {
          localClientPendingPromises.delete(localCacheKey);
          resolveLocalClient = pendingEntry!.resolve;
          rejectLocalClient = pendingEntry!.reject;
        }
        if (isSpawner) {
          const timeoutMs = 60_000;
          const intervalMs = 500;

          // Prepare port: kill stale OpenViking, or auto-find free port if occupied by others
          const actualPort = await prepareLocalPort(cfg.port, api.logger);
          const baseUrl = `http://127.0.0.1:${actualPort}`;

          const pythonCmd = resolvePythonCommand(api.logger);

          // Inherit system environment; optionally override Go/Python paths via env vars
          const pathSep = IS_WIN ? ";" : ":";
          const { ALL_PROXY, all_proxy, HTTP_PROXY, http_proxy, HTTPS_PROXY, https_proxy, ...filteredEnv } = process.env;
          const env = {
            ...filteredEnv,
            PYTHONUNBUFFERED: "1",
            PYTHONWARNINGS: "ignore::RuntimeWarning",
            OPENVIKING_CONFIG_FILE: cfg.configPath,
            OPENVIKING_START_CONFIG: cfg.configPath,
            OPENVIKING_START_HOST: "127.0.0.1",
            OPENVIKING_START_PORT: String(actualPort),
            ...(process.env.OPENVIKING_GO_PATH && { PATH: `${process.env.OPENVIKING_GO_PATH}${pathSep}${process.env.PATH || ""}` }),
            ...(process.env.OPENVIKING_GOPATH && { GOPATH: process.env.OPENVIKING_GOPATH }),
            ...(process.env.OPENVIKING_GOPROXY && { GOPROXY: process.env.OPENVIKING_GOPROXY }),
          };
          // Run OpenViking server: use run_path on the module file to avoid RuntimeWarning from
          // "parent package import loads submodule before execution" (exit 3). Fallback to run_module with warning suppressed.
          const runpyCode = `import sys,os,warnings; warnings.filterwarnings('ignore', category=RuntimeWarning, message='.*sys.modules.*'); sys.argv=['openviking.server.bootstrap','--config',os.environ['OPENVIKING_START_CONFIG'],'--host',os.environ.get('OPENVIKING_START_HOST','127.0.0.1'),'--port',os.environ['OPENVIKING_START_PORT']]; import runpy, importlib.util; spec=importlib.util.find_spec('openviking.server.bootstrap'); (runpy.run_path(spec.origin, run_name='__main__') if spec and getattr(spec,'origin',None) else runpy.run_module('openviking.server.bootstrap', run_name='__main__', alter_sys=True))`;
          const child = spawn(
            pythonCmd,
            ["-c", runpyCode],
            { env, cwd: IS_WIN ? tmpdir() : "/tmp", stdio: ["ignore", "pipe", "pipe"] },
          );
          localProcess = child;
          const stderrChunks: string[] = [];
          let stderrCharCount = 0;
          let stderrDroppedChunks = 0;
          const pushStderrChunk = (chunk: string) => {
            if (!chunk) return;
            stderrChunks.push(chunk);
            stderrCharCount += chunk.length;
            while (
              stderrChunks.length > MAX_OPENVIKING_STDERR_LINES ||
              stderrCharCount > MAX_OPENVIKING_STDERR_CHARS
            ) {
              const dropped = stderrChunks.shift();
              if (!dropped) break;
              stderrCharCount -= dropped.length;
              stderrDroppedChunks += 1;
            }
          };
          const formatStderrOutput = () => {
            if (!stderrChunks.length && !stderrDroppedChunks) return "";
            const truncated =
              stderrDroppedChunks > 0
                ? `[truncated ${stderrDroppedChunks} earlier stderr chunk(s)]\n`
                : "";
            return `\n[openviking stderr]\n${truncated}${stderrChunks.join("\n")}`;
          };
          child.on("error", (err: Error) => api.logger.warn(`openviking: local server error: ${String(err)}`));
          child.stderr?.on("data", (chunk: Buffer) => {
            const s = String(chunk).trim();
            pushStderrChunk(s);
            if (cfg.logFindRequests) {
              api.logger.info(`[openviking-local] ${s}`);
            } else {
              api.logger.debug?.(`[openviking] ${s}`);
            }
          });
          child.on("exit", (code: number | null, signal: string | null) => {
            if (localProcess === child) {
              localProcess = null;
              localClientCache.delete(localCacheKey);
            }
            const out = formatStderrOutput();
            api.logger.warn(`openviking: subprocess exited (code=${code}, signal=${signal})${out}`);
          });
          try {
            await waitForHealthOrExit(baseUrl, timeoutMs, intervalMs, child);
            const client = new OpenVikingClient(
              baseUrl,
              cfg.apiKey,
              cfg.agentId,
              cfg.timeoutMs,
              tenantAccount,
              tenantUser,
              routingDebugLog,
            );
            localClientCache.set(localCacheKey, { client, process: child });
            resolveLocalClient!(client);
            rejectLocalClient = null;
            api.logger.info(
              `openviking: local server started (${baseUrl}, config: ${cfg.configPath})`,
            );
          } catch (err) {
            localProcess = null;
            child.kill("SIGTERM");
            markLocalUnavailable("startup failed", err);
            if (stderrChunks.length) {
              api.logger.warn(
                `openviking: startup failed (health check timeout or error).${formatStderrOutput()}`,
              );
            }
            throw err;
          }
        } else if (cfg.mode === "local") {
          // Defensive re-spawn: if we're not the designated spawner but there's
          // no valid local process, trigger a fresh spawn to recover from
          // scenarios like Gateway force-restart where the child process was
          // orphaned or exited silently.
          const cached = localClientCache.get(localCacheKey);
          const processAlive = cached?.process && cached.process.exitCode === null && !cached.process.killed;
          if (!processAlive) {
            const healthOk = await quickHealthCheck(`http://127.0.0.1:${cfg.port}`, 2000);
            if (!healthOk) {
              api.logger.warn(
                `openviking: no valid local process detected (isSpawner=false), triggering defensive re-spawn`,
              );
              const timeoutMs = 60_000;
              const intervalMs = 500;
              const actualPort = await prepareLocalPort(cfg.port, api.logger);
              const baseUrl = `http://127.0.0.1:${actualPort}`;
              const pythonCmd = resolvePythonCommand(api.logger);
              const pathSep = IS_WIN ? ";" : ":";
              const env = {
                ...process.env,
                PYTHONUNBUFFERED: "1",
                PYTHONWARNINGS: "ignore::RuntimeWarning",
                OPENVIKING_CONFIG_FILE: cfg.configPath,
                OPENVIKING_START_CONFIG: cfg.configPath,
                OPENVIKING_START_HOST: "127.0.0.1",
                OPENVIKING_START_PORT: String(actualPort),
                ...(process.env.OPENVIKING_GO_PATH && { PATH: `${process.env.OPENVIKING_GO_PATH}${pathSep}${process.env.PATH || ""}` }),
                ...(process.env.OPENVIKING_GOPATH && { GOPATH: process.env.OPENVIKING_GOPATH }),
                ...(process.env.OPENVIKING_GOPROXY && { GOPROXY: process.env.OPENVIKING_GOPROXY }),
              };
              const runpyCode = `import sys,os,warnings; warnings.filterwarnings('ignore', category=RuntimeWarning, message='.*sys.modules.*'); sys.argv=['openviking.server.bootstrap','--config',os.environ['OPENVIKING_START_CONFIG'],'--host',os.environ.get('OPENVIKING_START_HOST','127.0.0.1'),'--port',os.environ['OPENVIKING_START_PORT']]; import runpy, importlib.util; spec=importlib.util.find_spec('openviking.server.bootstrap'); (runpy.run_path(spec.origin, run_name='__main__') if spec and getattr(spec,'origin',None) else runpy.run_module('openviking.server.bootstrap', run_name='__main__', alter_sys=True))`;
              const child = spawn(
                pythonCmd,
                ["-c", runpyCode],
                { env, cwd: IS_WIN ? tmpdir() : "/tmp", stdio: ["ignore", "pipe", "pipe"] },
              );
              localProcess = child;
              child.on("error", (err: Error) => api.logger.warn(`openviking: local server error (re-spawn): ${String(err)}`));
              child.stderr?.on("data", (chunk: Buffer) => {
                api.logger.debug?.(`[openviking-respawn] ${String(chunk).trim()}`);
              });
              child.on("exit", (code: number | null, signal: string | null) => {
                if (localProcess === child) {
                  localProcess = null;
                  localClientCache.delete(localCacheKey);
                }
                api.logger.warn(`openviking: re-spawned subprocess exited (code=${code}, signal=${signal})`);
              });
              try {
                await waitForHealthOrExit(baseUrl, timeoutMs, intervalMs, child);
                const client = new OpenVikingClient(baseUrl, cfg.apiKey, cfg.agentId, cfg.timeoutMs);
                localClientCache.set(localCacheKey, { client, process: child });
                if (resolveLocalClient) {
                  resolveLocalClient(client);
                  rejectLocalClient = null;
                }
                api.logger.info(
                  `openviking: local server re-spawned successfully (${baseUrl}, config: ${cfg.configPath})`,
                );
              } catch (err) {
                localProcess = null;
                child.kill("SIGTERM");
                markLocalUnavailable("re-spawn failed", err);
                api.logger.warn(`openviking: defensive re-spawn failed: ${String(err)}`);
                throw err;
              }
            } else {
              api.logger.info(`openviking: local process healthy on port ${cfg.port} (isSpawner=false)`);
            }
          } else {
            await (await getClient()).healthCheck().catch(() => {});
            api.logger.info(
              `openviking: initialized via cache (url: ${cfg.baseUrl}, targetUri: ${cfg.targetUri})`,
            );
          }
        } else {
          await (await getClient()).healthCheck().catch(() => {});
          api.logger.info(
            `openviking: initialized (url: ${cfg.baseUrl}, targetUri: ${cfg.targetUri}, search: hybrid endpoint)`,
          );
        }
      },
      stop: () => {
        if (localProcess) {
          localProcess.kill("SIGTERM");
          localClientCache.delete(localCacheKey);
          localClientPendingPromises.delete(localCacheKey);
          localProcess = null;
          api.logger.info("openviking: local server stopped");
        } else {
          api.logger.info("openviking: stopped");
        }
      },
    });
  },
};

/** Estimate token count using chars/4 heuristic (adequate for budget enforcement). */
export function estimateTokenCount(text: string): number {
  if (!text) return 0;
  return Math.ceil(text.length / 4);
}

export type BuildMemoryLinesOptions = {
  recallPreferAbstract: boolean;
  recallMaxContentChars: number;
};

async function resolveMemoryContent(
  item: FindResultItem,
  readFn: (uri: string) => Promise<string>,
  options: BuildMemoryLinesOptions,
): Promise<string> {
  let content: string;

  if (options.recallPreferAbstract && item.abstract?.trim()) {
    content = item.abstract.trim();
  } else if (item.level === 2) {
    try {
      const fullContent = await readFn(item.uri);
      content =
        fullContent && typeof fullContent === "string" && fullContent.trim()
          ? fullContent.trim()
          : (item.abstract?.trim() || item.uri);
    } catch {
      content = item.abstract?.trim() || item.uri;
    }
  } else {
    content = item.abstract?.trim() || item.uri;
  }

  if (content.length > options.recallMaxContentChars) {
    content = content.slice(0, options.recallMaxContentChars) + "...";
  }

  return content;
}

export async function buildMemoryLines(
  memories: FindResultItem[],
  readFn: (uri: string) => Promise<string>,
  options: BuildMemoryLinesOptions,
): Promise<string[]> {
  const lines: string[] = [];
  for (const item of memories) {
    const content = await resolveMemoryContent(item, readFn, options);
    lines.push(`- [${item.category ?? "memory"}] ${content}`);
  }
  return lines;
}

export type BuildMemoryLinesWithBudgetOptions = BuildMemoryLinesOptions & {
  recallTokenBudget: number;
};

/**
 * Build memory lines with a token budget constraint.
 *
 * The first memory is always included even if its token count exceeds the
 * remaining budget. This is intentional (spec Section 6.2): with
 * `recallMaxContentChars=500`, a single line is at most ~128 tokens — well
 * within the 2000-token default budget — so overshoot is bounded and
 * guarantees at least one memory is surfaced.
 */
export async function buildMemoryLinesWithBudget(
  memories: FindResultItem[],
  readFn: (uri: string) => Promise<string>,
  options: BuildMemoryLinesWithBudgetOptions,
): Promise<{ lines: string[]; estimatedTokens: number }> {
  let budgetRemaining = options.recallTokenBudget;
  const lines: string[] = [];
  let totalTokens = 0;

  for (const item of memories) {
    if (budgetRemaining <= 0) {
      break;
    }

    const content = await resolveMemoryContent(item, readFn, options);
    const line = `- [${item.category ?? "memory"}] ${content}`;
    const lineTokens = estimateTokenCount(line);

    // First line is always included even if it exceeds the budget (spec §6.2).
    if (lineTokens > budgetRemaining && lines.length > 0) {
      break;
    }

    lines.push(line);
    totalTokens += lineTokens;
    budgetRemaining -= lineTokens;
  }

  return { lines, estimatedTokens: totalTokens };
}

export default contextEnginePlugin;
